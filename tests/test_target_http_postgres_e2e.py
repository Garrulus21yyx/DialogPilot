import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx

from application.conversation_state import ConversationOwner
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_run import TargetRunCoordinator
from application.target_encoder_artifact import load_target_text_encoder_artifact
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.conversation_agent import ConversationAgent
from application.target_understanding import (
    CascadedTargetUnderstanding,
    StateBoundTargetUnderstanding,
)
from core.auth import Principal
from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_admission import PostgresStartOutbox, StartOutboxDispatcher
from infrastructure.postgres_conversation import PostgresInvocationRepository
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from infrastructure.postgres_target_run import (
    PostgresTargetRunBinder,
    PostgresTargetRunStore,
)
from infrastructure.target_chat_adapters import (
    PostgresTargetAdmission,
    PostgresTargetPublication,
)
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.target_workflow_execution import TargetWorkflowExecutor


class _ScenarioTools:
    def __init__(self):
        self.calls = []
        self.fail_catalog_for = set()
        self.unknown_refund_for = set()
        self.uncertain_refunds = set()

    async def execute_for_agent(
        self, name, params, *, agent_type, context, approved=False, call_id=None,
    ):
        self.calls.append((name, dict(params), agent_type, dict(context), approved))
        if name == "catalog_search" and context["conversation_id"] in self.fail_catalog_for:
            return _result(name, success=False, status="timeout")
        if name == "order_lookup":
            status = (
                "paid"
                if any(
                    marker in context["conversation_id"]
                    for marker in ("order-cancel", "address-change")
                )
                else "SHIPPED"
            )
            return _result(
                name,
                data={"order_id": params["order_id"], "status": status, "version": 3},
                authority="order.current_state",
                text=f"订单 {params['order_id']} 已发货。",
            )
        if name == "knowledge_search":
            query = str(params["query"])
            return _result(
                name,
                data={
                    "source_id": "policy:billing-v1",
                    "content": f"已验证政策：{query}",
                },
                authority="knowledge.active_source",
                text=f"根据当前政策处理：{query}",
            )
        if name == "refund_status":
            if context["conversation_id"] in self.uncertain_refunds:
                return _result(
                    name,
                    data={
                        "refund_id": "refund-reconciled-1",
                        "order_id": params["order_id"],
                        "operation_key": params["operation_key"],
                        "status": "REQUESTED",
                    },
                    authority="refund.current_state",
                    text="退款申请已提交。",
                )
            return _result(
                name,
                data={"order_id": params["order_id"], "status": "PROCESSING"},
                authority="refund.current_state",
                text=f"退款 {params['order_id']} 正在处理中。",
            )
        if name == "refund_eligibility_check":
            return _result(
                name,
                data={"order_id": params["order_id"], "eligible": True, "order_version": 3},
                authority="refund.eligibility",
                text=f"订单 {params['order_id']} 符合退款条件，尚未提交。",
            )
        if name == "media_read":
            return _result(
                name,
                data={"visible_text": "PX-200"},
                authority="media.visible_text",
                text="图片中可见型号 PX-200。",
            )
        if name == "catalog_search":
            return _result(
                name,
                data={"model": "PX-200", "canonical_id": "product:PX-200"},
                authority="product.canonical_model",
                text="已确认商品型号为 PX-200。",
            )
        if name == "account_security_event_list":
            return _result(
                name,
                data=[{
                    "event_id": "security-event-1",
                    "event_type": "suspicious_login",
                    "severity": "critical",
                    "summary": "检测到未知设备登录",
                    "occurred_at": "2026-09-03T00:00:00+00:00",
                }],
                authority="account.security_events",
                text="检测到一条未知设备登录记录。",
            )
        if name == "account_security_state":
            return _result(
                name,
                data={
                    "status": "active",
                    "version": 8,
                    "updated_at": "2026-09-03T00:00:00+00:00",
                },
                authority="account.current_state",
                text="当前账户处于可冻结状态。",
            )
        if name == "support_ticket_create":
            assert approved is True
            return _result(
                name,
                data={"ticket_id": "ticket-target-1", "status": "open"},
                authority="support.handoff_action",
                text="",
                effect_status="committed",
                receipt_id="ticket-target-1",
            )
        if name == "refund_request_create":
            assert approved is True
            if context["conversation_id"] in self.unknown_refund_for:
                self.uncertain_refunds.add(context["conversation_id"])
                return _result(
                    name,
                    success=False,
                    status="timeout",
                    effect_status="outcome_unknown",
                )
            return _result(
                name,
                data={
                    "refund_id": "refund-target-1",
                    "order_id": params["order_id"],
                    "status": "REQUESTED",
                },
                authority="refund.request_action",
                text="退款申请已提交。",
                effect_status="committed",
                receipt_id="refund-target-1",
            )
        if name == "order_cancel":
            assert approved is True
            return _result(
                name,
                data={
                    "cancellation_id": "cancel-target-1",
                    "order_id": params["order_id"],
                    "status": "cancelled",
                    "order_version": 4,
                },
                authority="order.cancel_action",
                text="订单已取消。",
                effect_status="committed",
                receipt_id="cancel-target-1",
            )
        if name == "shipping_address_change":
            assert approved is True
            return _result(
                name,
                data={
                    "change_id": "address-target-1",
                    "order_id": params["order_id"],
                    "new_address": params["new_address"],
                    "status": "updated",
                    "order_version": 4,
                },
                authority="order.shipping_address_action",
                text="收货地址已修改。",
                effect_status="committed",
                receipt_id="address-target-1",
            )
        if name == "account_freeze":
            assert approved is True
            return _result(
                name,
                data={
                    "freeze_id": "freeze-target-1",
                    "status": "frozen",
                    "account_version": 9,
                },
                authority="account.freeze_action",
                text="账户已冻结。",
                effect_status="committed",
                receipt_id="freeze-target-1",
            )
        return _result(name, success=False, status="denied")


class _ScenarioConversationProvider:
    version = "scenario-semantic-provider-v1"

    def __init__(self):
        self.calls = []

    async def plan(self, payload):
        self.calls.append(payload)
        message = str(payload["message"])
        if "异常登录" in message or "账号被盗" in message:
            goals = [{"goal_id": "security", "kind": "security_review"}]
            if "退款" in message:
                goals.append({
                    "goal_id": "refund",
                    "kind": "execute_refund",
                    "order_id": "DP8080",
                })
            return {"status": "resolved", "goals": goals}
        if "冻结账户" in message:
            return {"status": "resolved", "goals": [{"kind": "freeze_account"}]}
        if "人工客服" in message:
            return {"status": "resolved", "goals": [{"kind": "human_handoff"}]}
        if "地址改成" in message:
            return {
                "status": "resolved",
                "goals": [{
                    "kind": "change_address",
                    "order_id": "DP7654",
                    "new_address": "Berlin Example Street 9",
                }],
            }
        if "取消订单" in message:
            return {
                "status": "resolved",
                "goals": [{"kind": "cancel_order", "order_id": "DP4321"}],
            }
        if "商品型号" in message and "退款" in message:
            return {
                "status": "resolved",
                "goals": [
                    {"goal_id": "refund", "kind": "refund_status", "order_id": "DP1234"},
                    {"goal_id": "product", "kind": "product_identification"},
                ],
            }
        if "商品型号" in message:
            return {
                "status": "resolved",
                "goals": [{"kind": "product_identification"}],
            }
        if "电子发票" in message:
            return {"status": "resolved", "goals": [{"kind": "invoice_qa"}]}
        if "退款政策" in message:
            return {"status": "resolved", "goals": [{"kind": "refund_policy"}]}
        if "能退款" in message:
            return {
                "status": "resolved",
                "goals": [{"kind": "refund_eligibility", "order_id": "DP7777"}],
            }
        if "退款" in message and "状态" not in message:
            order_id = "DP9012" if "DP9012" in message else "DP5678" if "DP5678" in message else "DP1234"
            return {
                "status": "resolved",
                "goals": [{"kind": "execute_refund", "order_id": order_id}],
            }
        if "物流" in message:
            return {
                "status": "resolved",
                "goals": [{"kind": "order_status", "order_id": "DP1234"}],
            }
        if "走到哪一步" in message:
            return {
                "status": "resolved",
                "goals": [{
                    "goal_id": "semantic-order",
                    "kind": "order_status",
                    "order_id": "DP2468",
                }],
            }
        return {
            "status": "insufficient_context",
            "missing_fields": ["customer_service_goal"],
        }


def _result(
    name,
    *,
    success=True,
    status="success",
    data=None,
    authority="",
    text="",
    effect_status="none",
    receipt_id="",
    receipt_schema_version="action-receipt-v1",
):
    return SimpleNamespace(
        success=success,
        data=data,
        authority=authority,
        receipt_id=receipt_id,
        call_id=f"call:{name}",
        output_schema_version=f"{name}-output-v1",
        output_for_model=text,
        status=status,
        effect_status=effect_status,
        receipt_schema_version=receipt_schema_version,
    )


def test_six_target_scenarios_cross_real_http_and_postgres_boundaries(
    postgres_database_url,
    monkeypatch,
):
    from api import main

    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    tools = _ScenarioTools()
    conversation_provider = _ScenarioConversationProvider()
    read_executor = TargetToolExecutor(tools)
    workflow_executor = TargetWorkflowExecutor(pool, tools)
    registry = build_default_capability_registry("tenant-target-e2e")
    state_store = PostgresConversationStateStore(pool)
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-target-e2e")
    main.app.dependency_overrides[main.get_principal] = lambda: Principal(
        "user-target-e2e", frozenset({"chat"}),
    )
    prefix = uuid4().hex

    async def run():
        checkpoint_owner = AsyncPostgresCheckpointOwner(
            postgres_database_url, setup=True,
        )
        async with checkpoint_owner as checkpointer:
            runtime = TargetChatApplication(
                manager=TargetConversationManager(
                    state_store=state_store,
                    registry=registry,
                    understanding=CascadedTargetUnderstanding(
                        StateBoundTargetUnderstanding(),
                        ConversationAgent(conversation_provider),
                        encoder=TargetEncoderUnderstanding(
                            load_target_text_encoder_artifact(
                                __import__("pathlib").Path(__file__).resolve().parents[1]
                                / "artifacts" / "target-encoder-zh-v2"
                            )
                        ),
                    ),
                    orchestration=OrchestrationRuntime(
                        direct_executor=read_executor,
                        domain_workers={
                            "billing_refund": read_executor,
                            "product_technical": read_executor,
                            "human_service": workflow_executor,
                        },
                        workflow_executor=workflow_executor,
                        checkpointer=checkpointer,
                    ),
                ),
                admission=PostgresTargetAdmission(pool, durable=True),
                publication=PostgresTargetPublication(PostgresResponseDeliveryService(
                    pool, resume_binding_secret="target-e2e-secret",
                )),
                bundle_version=registry.bundle_version,
            )
            run_store = PostgresTargetRunStore(pool)
            coordinator = TargetRunCoordinator(
                runtime,
                dispatcher=StartOutboxDispatcher(
                    PostgresStartOutbox(pool),
                    PostgresInvocationRepository(pool),
                    PostgresTargetRunBinder(pool),
                ),
                store=run_store,
                worker_id=f"target-e2e-{prefix}",
                heartbeat_seconds=1,
                execution_lease_seconds=10,
            )
            monkeypatch.setattr(main, "_target_chat_runtime", runtime)
            monkeypatch.setattr(main, "_target_run_coordinator", coordinator)
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://target.test",
            ) as client:
                async def chat(
                    case, message, *, assets=(), request_suffix="request", **extra,
                ):
                    payload = {
                        "message": message,
                        "conv_id": f"{prefix}-{case}",
                        "request_id": f"{prefix}-{case}-{request_suffix}",
                        "asset_ids": list(assets),
                        **extra,
                    }
                    response = await client.post("/chat", json=payload)
                    if (
                        response.status_code == 202
                        and response.json().get("outcome") == "accepted"
                    ):
                        await coordinator.pump_once()
                        response = await client.post("/chat", json=payload)
                    return response

                order = await chat("order", "查订单 DP1234 物流")
                eligibility = await chat(
                    "eligibility", "订单 DP7777 能退款吗？",
                )
                refund_policy = await chat(
                    "refund-policy", "退款政策和一般时效是什么？",
                )
                invoice = await chat("invoice", "电子发票怎么开？")
                encoder_refund = await chat(
                    "encoder-refund", "确认 RF3100 的退回进展",
                )
                planned_order = await chat(
                    "semantic-order", "帮我看看 DP2468 走到哪一步了",
                )
                product = await chat("product", "识别这张图的商品型号", assets=("IMG9",))
                refund_precheck = await chat("refund", "把订单 DP1234 退款")
                assert refund_precheck.status_code == 202, refund_precheck.text
                refund_precheck_replay = await chat("refund", "把订单 DP1234 退款")
                approval = refund_precheck.json()["signal_id"]
                refund_committed = await chat(
                    "refund",
                    "确认提交退款",
                    request_suffix="approval",
                    approval_id=approval,
                    approved=True,
                )
                cancel_precheck = await chat(
                    "order-cancel", "取消订单 DP4321",
                )
                cancel_signal = cancel_precheck.json()["signal_id"]
                cancel_committed = await chat(
                    "order-cancel",
                    "确认取消订单",
                    request_suffix="approval",
                    approval_id=cancel_signal,
                    approved=True,
                )
                address_precheck = await chat(
                    "address-change",
                    "把订单 DP7654 的地址改成 Berlin Example Street 9",
                )
                address_signal = address_precheck.json()["signal_id"]
                address_committed = await chat(
                    "address-change",
                    "确认修改地址",
                    request_suffix="approval",
                    approval_id=address_signal,
                    approved=True,
                )
                security_review = await chat(
                    "security-review",
                    "这次异常登录不是我操作的，帮我查一下",
                )
                security_preemption = await chat(
                    "security-preemption",
                    "账号被盗，帮我查异常登录，同时把订单 DP8080 退款",
                )
                assert security_preemption.status_code == 200
                security_payload = security_preemption.json()
                assert security_payload["routing_reason"] == (
                    "SECURITY_PREEMPTED_NONESSENTIAL_WRITES"
                )
                assert "本轮未启动其他高风险业务操作" in security_payload["response"]
                assert not any(
                    name == "refund_eligibility_check"
                    and params.get("order_id") == "DP8080"
                    for name, params, *_ in tools.calls
                )
                freeze_precheck = await chat(
                    "account-freeze", "立即冻结账户",
                )
                freeze_signal = freeze_precheck.json()["signal_id"]
                freeze_committed = await chat(
                    "account-freeze",
                    "确认冻结账户",
                    request_suffix="approval",
                    approval_id=freeze_signal,
                    approved=True,
                )
                refund_commit_replay = await chat(
                    "refund",
                    "确认提交退款",
                    request_suffix="approval",
                    approval_id=approval,
                    approved=True,
                )
                changed_approval_replay = await chat(
                    "refund",
                    "确认提交退款",
                    request_suffix="approval",
                    approval_id=approval,
                    approved=False,
                )
                stale_approval = await chat(
                    "refund",
                    "再次确认",
                    request_suffix="stale-approval",
                    approval_id=approval,
                    approved=True,
                )
                cross_conversation_approval = await chat(
                    "approval-other-conversation",
                    "确认提交退款",
                    approval_id=approval,
                    approved=True,
                )
                refund_decline = await chat("refund-decline", "把订单 DP5678 退款")
                decline_signal = refund_decline.json()["signal_id"]
                refund_declined = await chat(
                    "refund-decline",
                    "不退款了",
                    request_suffix="decline",
                    approval_id=decline_signal,
                    approved=False,
                )
                unknown_conv = f"{prefix}-refund-unknown"
                tools.unknown_refund_for.add(unknown_conv)
                refund_unknown_prepare = await chat(
                    "refund-unknown", "把订单 DP9012 退款",
                )
                unknown_signal = refund_unknown_prepare.json()["signal_id"]
                refund_unknown = await chat(
                    "refund-unknown",
                    "确认提交退款",
                    request_suffix="approval",
                    approval_id=unknown_signal,
                    approved=True,
                )
                unknown_state = state_store.load(
                    registry.tenant_id,
                    "user-target-e2e",
                    unknown_conv,
                )
                assert unknown_state.workstreams[0].status.value == "RECONCILING"
                assert unknown_state.workstreams[0].phase == "RECONCILE"
                refund_reconciled = await chat(
                    "refund-unknown",
                    "查询刚才退款结果",
                    request_suffix="reconcile",
                    approval_id=unknown_signal,
                    approved=True,
                )
                multi = await chat(
                    "multi", "退款 DP1234 状态，还有这个商品型号", assets=("IMG10",),
                )
                failed_conv = f"{prefix}-partial"
                tools.fail_catalog_for.add(failed_conv)
                partial = await chat(
                    "partial", "退款 DP1234 状态，还有这个商品型号", assets=("IMG11",),
                )
                handoff = await chat("handoff", "我要人工客服处理这个问题")
                return (
                    order, eligibility, refund_policy, invoice,
                    encoder_refund, planned_order, product,
                    refund_precheck, refund_precheck_replay,
                    refund_committed, refund_commit_replay, stale_approval,
                    changed_approval_replay, cross_conversation_approval,
                    refund_decline, refund_declined, multi, partial, handoff,
                    refund_unknown_prepare, refund_unknown, refund_reconciled,
                    cancel_precheck, cancel_committed,
                    address_precheck, address_committed,
                    security_review, freeze_precheck, freeze_committed,
                )

    try:
        responses = asyncio.run(run())
        for name, response in zip(
            (
                "order", "eligibility", "refund-policy", "invoice",
                "encoder-refund", "semantic-order", "product",
                "refund-precheck", "refund-replay",
                "refund-commit", "refund-commit-replay", "stale-approval",
                "changed-approval-replay", "cross-conversation-approval",
                "refund-decline", "refund-declined", "multi", "partial", "handoff",
                "refund-unknown-prepare", "refund-unknown", "refund-reconciled",
                "cancel-precheck", "cancel-committed",
                "address-precheck", "address-committed",
                "security-review", "freeze-precheck", "freeze-committed",
            ),
            responses,
        ):
            expected = (
                202
                if name in {
                    "refund-precheck", "refund-replay", "refund-decline",
                    "refund-unknown-prepare", "refund-unknown",
                    "cancel-precheck",
                    "address-precheck",
                    "freeze-precheck",
                }
                else 409 if name in {
                    "stale-approval", "changed-approval-replay",
                    "cross-conversation-approval",
                } else 200
            )
            assert response.status_code == expected, f"{name}: {response.text}"
        (
            order, eligibility, refund_policy, invoice,
            encoder_refund, planned_order, product,
            refund_precheck, refund_precheck_replay,
            refund_committed, refund_commit_replay, stale_approval,
            changed_approval_replay, cross_conversation_approval,
            refund_decline, refund_declined, multi, partial, handoff,
            refund_unknown_prepare, refund_unknown, refund_reconciled,
            cancel_precheck, cancel_committed,
            address_precheck, address_committed,
            security_review, freeze_precheck, freeze_committed,
        ) = (
            item.json() for item in responses
        )
        assert order["routing_disposition"] == "direct"
        assert eligibility["routing_disposition"] == "direct"
        assert eligibility["agent_types"] == ["billing_refund"]
        assert "符合退款条件" in eligibility["response"]
        assert refund_policy["routing_disposition"] == "knowledge_qa"
        assert "当前政策" in refund_policy["response"]
        assert invoice["routing_disposition"] == "knowledge_qa"
        assert "电子发票" in invoice["response"]
        assert encoder_refund["routing_reason"] == "ENCODER_FAST_PATH_ACCEPTED"
        assert encoder_refund["routing_disposition"] == "direct"
        assert "RF3100" in encoder_refund["response"]
        assert encoder_refund["evaluation_trace"]["cost"] == {
            "conversation_planner_invoked": False,
            "work_item_count": 1,
            "latency_ms": encoder_refund["latency_ms"],
        }
        assert planned_order["routing_disposition"] == "direct"
        assert "DP2468" in planned_order["response"]
        assert planned_order["evaluation_trace"]["cost"][
            "conversation_planner_invoked"
        ] is True
        planned_messages = {
            str(item["message"]) for item in conversation_provider.calls
        }
        assert "帮我看看 DP2468 走到哪一步了" in planned_messages
        assert "确认 RF3100 的退回进展" not in planned_messages
        assert "PX-200" in product["response"]
        assert refund_precheck["outcome"] == "needs_input"
        assert refund_precheck_replay == refund_precheck
        assert "refund-target-1" in refund_committed["response"]
        assert refund_commit_replay == refund_committed
        assert stale_approval["code"] == "APPROVAL_SIGNAL_CONFLICT"
        assert changed_approval_replay["code"] == "IDEMPOTENCY_CONFLICT"
        assert cross_conversation_approval["code"] == "APPROVAL_SIGNAL_CONFLICT"
        assert "未执行任何业务写入" in refund_declined["response"]
        assert refund_unknown["outcome"] == "reconciling"
        assert "refund-reconciled-1" in refund_reconciled["response"]
        assert cancel_precheck["outcome"] == "needs_input"
        assert "cancel-target-1" in cancel_committed["response"]
        cancel_calls = [item for item in tools.calls if item[0] == "order_cancel"]
        assert len(cancel_calls) == 1
        assert cancel_calls[0][2] == "general"
        assert address_precheck["outcome"] == "needs_input"
        assert "address-target-1" in address_committed["response"]
        address_calls = [
            item for item in tools.calls
            if item[0] == "shipping_address_change"
        ]
        assert len(address_calls) == 1
        assert address_calls[0][1]["new_address"] == "Berlin Example Street 9"
        assert address_calls[0][2] == "general"
        assert security_review["routing_disposition"] == "direct"
        assert "未知设备登录" in security_review["response"]
        assert freeze_precheck["outcome"] == "needs_input"
        assert "freeze-target-1" in freeze_committed["response"]
        freeze_calls = [item for item in tools.calls if item[0] == "account_freeze"]
        assert len(freeze_calls) == 1
        assert freeze_calls[0][1] == {"expected_account_version": 8}
        assert freeze_calls[0][2] == "account_security"
        refund_write_calls = [
            item for item in tools.calls if item[0] == "refund_request_create"
        ]
        assert len(refund_write_calls) == 2
        assert sum(
            item[3]["conversation_id"] == f"{prefix}-refund-unknown"
            for item in refund_write_calls
        ) == 1
        assert multi["routing_disposition"] == "mixed"
        assert len(multi["agent_outcomes"]) == 2
        assert partial["verification_status"] == "partial"
        assert "refund" in partial["response"]
        assert handoff["handoff_created"] is True
        assert handoff["routing_disposition"] == "handoff"
        assert handoff["ticket_id"] == "ticket-target-1"
        handoff_state = state_store.load(
            registry.tenant_id,
            "user-target-e2e",
            f"{prefix}-handoff",
        )
        assert handoff_state.owner is ConversationOwner.HUMAN
        assert len(handoff_state.workstreams) == 1
        assert handoff_state.workstreams[0].phase == "COMPLETE"
        assert handoff_state.workstreams[0].status.value == "COMPLETED"
        declined_state = state_store.load(
            registry.tenant_id,
            "user-target-e2e",
            f"{prefix}-refund-decline",
        )
        assert declined_state.workstreams[0].status.value == "CANCELLED"
        reconciled_state = state_store.load(
            registry.tenant_id,
            "user-target-e2e",
            f"{prefix}-refund-unknown",
        )
        assert reconciled_state.workstreams[0].status.value == "COMPLETED"
    finally:
        main.app.dependency_overrides.clear()
        monkeypatch.setattr(main, "_target_chat_runtime", None)
        pool.close()
