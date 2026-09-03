import asyncio
import inspect
from types import SimpleNamespace

from application.chat_application import ChatCommand, Completed, Failed
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.target_chat_application import (
    PublishedTargetResponse,
    TargetAdmission,
    TargetAdmissionStatus,
    TargetChatApplication,
)
from application.target_conversation_manager import TargetConversationManager
from application.structured_target_router import (
    CascadedTargetUnderstanding,
    StructuredTargetCommandRouter,
)
from application.target_understanding import BoundedTargetUnderstanding
from core.identity import IdentityFactory
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.target_chat_adapters import PostgresTargetAdmission


class _Admission:
    def __init__(self):
        self.records = {}

    def admit(self, command, identity, *, bundle_version):
        key = str(identity.invocation_key)
        fingerprint = (command.message, command.asset_ids, bundle_version)
        existing = self.records.get(key)
        if existing is None:
            self.records[key] = fingerprint
            return TargetAdmission(TargetAdmissionStatus.CREATED)
        if existing == fingerprint:
            return TargetAdmission(TargetAdmissionStatus.EXISTING)
        return TargetAdmission(
            TargetAdmissionStatus.CONFLICT,
            {"invocation_key": key},
        )


class _Publication:
    def __init__(self):
        self.responses = {}

    def completed(self, identity):
        return self.responses.get(str(identity.invocation_key))

    def publish(
        self,
        identity,
        *,
        response_text,
        public_response,
        bundle_version,
        evidence_sha256,
        verifier_status,
    ):
        key = str(identity.invocation_key)
        published = PublishedTargetResponse(f"response:{key}", 1, "selected")
        body = dict(public_response)
        body.update({
            "response_id": published.response_id,
            "response_seq": published.response_seq,
            "delivery_status": published.delivery_status,
        })
        self.responses[key] = Completed(published.response_id, body)
        return published


class _ToolManager:
    def __init__(self):
        self.calls = []

    async def execute_for_agent(
        self, name, params, *, agent_type, context, approved=False, call_id=None,
    ):
        self.calls.append((name, dict(params), agent_type, dict(context)))
        return SimpleNamespace(
            success=True,
            data={"order_id": params["order_id"], "status": "SHIPPED"},
            authority="order.current_state",
            receipt_id="",
            call_id=call_id,
            output_schema_version="order-view-v1",
            output_for_model="订单 DP1234 已发货。",
            status="success",
        )


def _application(understanding=None):
    tools = _ToolManager()
    executor = TargetToolExecutor(tools)
    registry = build_default_capability_registry("tenant-a")
    manager = TargetConversationManager(
        state_store=InMemoryConversationStateStore(),
        registry=registry,
        understanding=understanding or BoundedTargetUnderstanding(),
        orchestration=OrchestrationRuntime(
            direct_executor=executor,
            domain_workers={
                "billing_refund": executor,
                "product_technical": executor,
            },
        ),
    )
    return TargetChatApplication(
        manager=manager,
        admission=_Admission(),
        publication=_Publication(),
        bundle_version=registry.bundle_version,
        identity_factory=IdentityFactory(lambda: "generated"),
    ), tools


class _FailingSemanticProvider:
    version = "failing-semantic-provider-test-v1"

    async def route(self, payload):
        raise TimeoutError("provider unavailable")


def test_target_chat_direct_order_path_publishes_once_and_replays():
    application, tools = _application()
    command = ChatCommand(
        "查一下订单 DP1234 物流",
        "user-a",
        "tenant-a",
        "conversation-a",
        "request-a",
    )

    first = asyncio.run(application.handle(command))
    replay = asyncio.run(application.handle(command))

    assert isinstance(first, Completed)
    assert replay == first
    assert first.response["routing_disposition"] == "direct"
    assert first.response["verified"] is True
    assert "已发货" in first.response["response"]
    trace = first.response["evaluation_trace"]
    assert tuple(trace) == (
        "schema_version", "trigger", "artifact", "consumption",
        "state_side_effect", "outcome", "cost",
    )
    assert trace["artifact"]["route_mode"] == "DIRECT"
    assert trace["consumption"]["work_items"][0]["control_mode"] == "DIRECT"
    assert trace["cost"]["semantic_provider_invoked"] is False
    assert len(tools.calls) == 1
    assert tools.calls[0][3]["user_id"] == "user-a"
    assert tools.calls[0][1] == {"order_id": "DP1234"}


def test_target_chat_unclear_request_uses_zero_tool_clarification():
    application, tools = _application()
    outcome = asyncio.run(application.handle(ChatCommand(
        "帮帮我", "user-a", "tenant-a", "conversation-a", "request-b",
    )))

    assert isinstance(outcome, Completed)
    assert outcome.response["routing_disposition"] == "clarify"
    assert tools.calls == []


def test_target_chat_preserves_semantic_provider_failure_as_retryable_failure():
    application, tools = _application(CascadedTargetUnderstanding(
        BoundedTargetUnderstanding(),
        StructuredTargetCommandRouter(_FailingSemanticProvider()),
    ))

    outcome = asyncio.run(application.handle(ChatCommand(
        "帮我看看 DP1234 走到哪一步了",
        "user-a", "tenant-a", "conversation-a", "request-provider-failure",
    )))

    assert isinstance(outcome, Failed)
    assert outcome.code == "semantic_provider_unavailable"
    assert outcome.retryable is True
    assert tools.calls == []


def test_chat_composition_accessor_has_no_legacy_fallback_authority():
    from api import main

    source = inspect.getsource(main._chat_application)
    assert "_target_chat_runtime" in source
    assert "_durable_chat_coordinator" not in source
    assert "_core_chat_application" not in source


def test_http_chat_function_projects_target_completed_response(monkeypatch):
    from api import main
    from core.auth import Principal

    application, _tools = _application()
    monkeypatch.setattr(main, "_target_chat_runtime", application)
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-a")
    request = main.ChatRequest(
        message="查订单 DP1234 物流",
        conv_id="conversation-http",
        request_id="request-http",
    )
    principal = Principal("user-a", frozenset({"chat"}))

    response = asyncio.run(main.chat(request, principal))

    assert response.response_id.startswith("response:")
    assert response.routing_disposition == "direct"
    assert response.verified is True
    assert response.evaluation_trace["outcome"]["verifier_status"] == "PASS"


def test_postgres_target_admission_binds_without_legacy_start_outbox(
    postgres_database_url,
):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    suffix = __import__("uuid").uuid4().hex
    identity = IdentityFactory(lambda: suffix).create_invocation(
        tenant_id=f"tenant-{suffix}",
        user_id=f"user-{suffix}",
        conversation_id=f"conversation-{suffix}",
        request_id=f"request-{suffix}",
    )
    command = ChatCommand(
        "查订单 DP1234 物流",
        str(identity.user_id),
        str(identity.tenant_id),
        str(identity.conversation_id),
        str(identity.request_id),
    )
    try:
        admission = PostgresTargetAdmission(pool)
        first = admission.admit(command, identity, bundle_version="customer-service-v1")
        replay = admission.admit(command, identity, bundle_version="customer-service-v1")
        with pool.transaction() as connection:
            row = connection.execute("""
                SELECT admission_status,execution_runtime_kind,execution_run_id
                FROM dialogpilot_app.workflow_invocations
                WHERE invocation_key=%s
            """, (str(identity.invocation_key),)).fetchone()
            outbox_count = connection.execute("""
                SELECT count(*) FROM dialogpilot_app.workflow_start_outbox
                WHERE invocation_key=%s
            """, (str(identity.invocation_key),)).fetchone()[0]
        assert first.status is TargetAdmissionStatus.CREATED
        assert replay.status is TargetAdmissionStatus.EXISTING
        assert row == (
            "EXECUTION_BOUND", "target-langgraph", str(identity.invocation_key),
        )
        assert outbox_count == 0
    finally:
        pool.close()
