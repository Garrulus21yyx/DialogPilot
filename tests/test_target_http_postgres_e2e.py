import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx

from application.conversation_state import ConversationOwner
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_encoder_artifact import load_target_text_encoder_artifact
from application.target_encoder_understanding import TargetEncoderUnderstanding
from application.structured_target_router import (
    CascadedTargetUnderstanding,
    StructuredTargetCommandRouter,
)
from application.target_understanding import BoundedTargetUnderstanding
from core.auth import Principal
from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
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
            return _result(
                name,
                data={"order_id": params["order_id"], "status": "SHIPPED", "version": 3},
                authority="order.current_state",
                text=f"订单 {params['order_id']} 已发货。",
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
        return _result(name, success=False, status="denied")


class _ScenarioSemanticProvider:
    version = "scenario-semantic-provider-v1"

    def __init__(self):
        self.calls = []

    async def route(self, payload):
        self.calls.append(payload)
        if "走到哪一步" in str(payload["message"]):
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
    semantic_provider = _ScenarioSemanticProvider()
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
                        BoundedTargetUnderstanding(),
                        StructuredTargetCommandRouter(semantic_provider),
                        encoder=TargetEncoderUnderstanding(
                            load_target_text_encoder_artifact(
                                __import__("pathlib").Path(__file__).resolve().parents[1]
                                / "artifacts" / "target-encoder-zh-v1"
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
                admission=PostgresTargetAdmission(pool),
                publication=PostgresTargetPublication(PostgresResponseDeliveryService(
                    pool, resume_binding_secret="target-e2e-secret",
                )),
                bundle_version=registry.bundle_version,
            )
            monkeypatch.setattr(main, "_target_chat_runtime", runtime)
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://target.test",
            ) as client:
                async def chat(
                    case, message, *, assets=(), request_suffix="request", **extra,
                ):
                    return await client.post("/chat", json={
                        "message": message,
                        "conv_id": f"{prefix}-{case}",
                        "request_id": f"{prefix}-{case}-{request_suffix}",
                        "asset_ids": list(assets),
                        **extra,
                    })

                order = await chat("order", "查订单 DP1234 物流")
                encoder_refund = await chat(
                    "encoder-refund", "确认 RF3100 的退回进展",
                )
                semantic_order = await chat(
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
                    order, encoder_refund, semantic_order, product,
                    refund_precheck, refund_precheck_replay,
                    refund_committed, refund_commit_replay, stale_approval,
                    changed_approval_replay, cross_conversation_approval,
                    refund_decline, refund_declined, multi, partial, handoff,
                    refund_unknown_prepare, refund_unknown, refund_reconciled,
                )

    try:
        responses = asyncio.run(run())
        for name, response in zip(
            (
                "order", "encoder-refund", "semantic-order", "product",
                "refund-precheck", "refund-replay",
                "refund-commit", "refund-commit-replay", "stale-approval",
                "changed-approval-replay", "cross-conversation-approval",
                "refund-decline", "refund-declined", "multi", "partial", "handoff",
                "refund-unknown-prepare", "refund-unknown", "refund-reconciled",
            ),
            responses,
        ):
            expected = (
                202
                if name in {
                    "refund-precheck", "refund-replay", "refund-decline",
                    "refund-unknown-prepare", "refund-unknown",
                }
                else 409 if name in {
                    "stale-approval", "changed-approval-replay",
                    "cross-conversation-approval",
                } else 200
            )
            assert response.status_code == expected, f"{name}: {response.text}"
        (
            order, encoder_refund, semantic_order, product,
            refund_precheck, refund_precheck_replay,
            refund_committed, refund_commit_replay, stale_approval,
            changed_approval_replay, cross_conversation_approval,
            refund_decline, refund_declined, multi, partial, handoff,
            refund_unknown_prepare, refund_unknown, refund_reconciled,
        ) = (
            item.json() for item in responses
        )
        assert order["routing_disposition"] == "direct"
        assert encoder_refund["routing_reason"] == "ENCODER_FAST_PATH_ACCEPTED"
        assert encoder_refund["routing_disposition"] == "agent_task"
        assert "RF3100" in encoder_refund["response"]
        assert semantic_order["routing_disposition"] == "direct"
        assert "DP2468" in semantic_order["response"]
        assert len(semantic_provider.calls) == 1
        assert "PX-200" in product["response"]
        assert refund_precheck["outcome"] == "needs_input"
        assert refund_precheck_replay == refund_precheck
        assert "refund-target-1" in refund_committed["response"]
        assert refund_commit_replay == refund_committed
        assert stale_approval["code"] == "APPROVAL_SIGNAL_CONFLICT"
        assert changed_approval_replay["code"] == "IDEMPOTENCY_CONFLICT"
        assert cross_conversation_approval["code"] == "APPROVAL_SIGNAL_CONFLICT"
        assert "未执行退款操作" in refund_declined["response"]
        assert refund_unknown["outcome"] == "reconciling"
        assert "refund-reconciled-1" in refund_reconciled["response"]
        refund_write_calls = [
            item for item in tools.calls if item[0] == "refund_request_create"
        ]
        assert len(refund_write_calls) == 2
        assert sum(
            item[3]["conversation_id"] == f"{prefix}-refund-unknown"
            for item in refund_write_calls
        ) == 1
        assert multi["routing_disposition"] == "multi_domain"
        assert len(multi["agent_outcomes"]) == 2
        assert partial["verification_status"] == "partial"
        assert "refund" in partial["response"]
        assert handoff["handoff_created"] is True
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
