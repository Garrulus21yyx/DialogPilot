import asyncio
from types import SimpleNamespace
from uuid import uuid4

import httpx

from application.conversation_state import ConversationOwner
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
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
        return _result(name, success=False, status="denied")


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
                    understanding=BoundedTargetUnderstanding(),
                    orchestration=OrchestrationRuntime(
                        direct_executor=read_executor,
                        domain_workers={
                            "billing_refund": read_executor,
                            "product_technical": read_executor,
                            "human_service": workflow_executor,
                        },
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
                async def chat(case, message, *, assets=()):
                    return await client.post("/chat", json={
                        "message": message,
                        "conv_id": f"{prefix}-{case}",
                        "request_id": f"{prefix}-{case}-request",
                        "asset_ids": list(assets),
                    })

                order = await chat("order", "查订单 DP1234 物流")
                product = await chat("product", "识别这张图的商品型号", assets=("IMG9",))
                refund_precheck = await chat("refund", "把订单 DP1234 退款")
                multi = await chat(
                    "multi", "退款 DP1234 状态，还有这个商品型号", assets=("IMG10",),
                )
                failed_conv = f"{prefix}-partial"
                tools.fail_catalog_for.add(failed_conv)
                partial = await chat(
                    "partial", "退款 DP1234 状态，还有这个商品型号", assets=("IMG11",),
                )
                handoff = await chat("handoff", "我要人工客服处理这个问题")
                return order, product, refund_precheck, multi, partial, handoff

    try:
        responses = asyncio.run(run())
        for name, response in zip(
            ("order", "product", "refund", "multi", "partial", "handoff"),
            responses,
        ):
            assert response.status_code == 200, f"{name}: {response.text}"
        order, product, refund_precheck, multi, partial, handoff = (
            item.json() for item in responses
        )
        assert order["routing_disposition"] == "direct"
        assert "PX-200" in product["response"]
        assert "尚未提交" in refund_precheck["response"]
        assert "refund_request_create" not in [item[0] for item in tools.calls]
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
    finally:
        main.app.dependency_overrides.clear()
        monkeypatch.setattr(main, "_target_chat_runtime", None)
        pool.close()
