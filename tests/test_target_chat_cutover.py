import asyncio
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
    MissingInputSpec,
)
from application.chat_contracts import ChatCommand, Completed, Conflict, Failed, NeedsInput
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
from application.conversation_agent import ConversationAgent
from application.target_understanding import (
    CascadedTargetUnderstanding,
    StateBoundTargetUnderstanding,
)
from core.identity import IdentityFactory
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.target_chat_adapters import PostgresTargetAdmission


class _Admission:
    def __init__(self):
        self.records = {}

    def admit(self, command, identity, *, bundle_version):
        key = str(identity.invocation_key)
        fingerprint = (
            command.message,
            command.asset_ids,
            command.interaction_id,
            command.interaction_version,
            command.interaction_values,
            bundle_version,
        )
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
        expected_work_controls=(),
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

    def publish_interaction(
        self,
        identity,
        *,
        signal_id,
        signal_version,
        challenge,
        resume_schema,
        expires_at,
    ):
        key = str(identity.invocation_key)
        published = PublishedTargetResponse(f"interaction:{key}", 1, "selected")
        self.responses[key] = NeedsInput(
            str(identity.workflow_run_id),
            signal_id,
            str(resume_schema.get("interaction_kind", "APPROVAL")),
            expires_at,
            published.response_id,
        )
        return published


class _MissingThenReadExecutor:
    def __init__(self):
        self.calls = []

    async def __call__(self, context):
        item = context.work_item
        arguments = {argument.name: argument.value for argument in item.arguments}
        self.calls.append(arguments)
        if "verification_reference" not in arguments:
            return AgentResult(
                item.work_item_id,
                item.owner_agent,
                AgentResultStatus.NEEDS_USER_INPUT,
                "VERIFICATION_REFERENCE_REQUIRED",
                "missing-read-test-v1",
                missing_inputs=(MissingInputSpec(
                    "verification_reference",
                    item.work_item_id,
                    "VERIFICATION_REFERENCE_REQUIRED",
                    "string",
                    "请提供订单核验信息。",
                ),),
            )
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "ORDER_READ",
            "missing-read-test-v1",
            facts=(FactRecord(
                "order:DP1234",
                "order.current_state",
                '{"status":"SHIPPED"}',
                FactSourceKind.VERIFIED_STATE,
                "receipt:order-read",
                "order_lookup",
                "order-view-v1",
                datetime.now(timezone.utc),
            ),),
            candidate_response="订单已发货。",
        )


class _ToolManager:
    def __init__(self):
        self.calls = []

    async def execute_for_agent(
        self, name, params, *, agent_type, context, approved=False, call_id=None,
    ):
        self.calls.append((name, dict(params), agent_type, dict(context)))
        return SimpleNamespace(
            success=True,
            tool_name=name,
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
        understanding=understanding or _default_understanding(),
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

    async def plan(self, payload):
        raise TimeoutError("provider unavailable")


class _ChatPlanningProvider:
    version = "chat-planning-provider-test-v1"

    async def plan(self, payload):
        message = str(payload["message"])
        if "DP1234" in message:
            return {
                "status": "resolved",
                "goals": [{"kind": "order_status", "order_id": "DP1234"}],
            }
        return {
            "status": "insufficient_context",
            "missing_fields": ["customer_service_goal"],
        }


def _default_understanding():
    return CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(),
        ConversationAgent(_ChatPlanningProvider()),
    )


def test_target_chat_out_of_scope_publishes_once_without_worker_execution():
    class ScopeProvider:
        version = "scope-test-v1"

        async def plan(self, payload):
            return {"status": "out_of_scope"}

    application, tools = _application(CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(), ConversationAgent(ScopeProvider()),
    ))
    command = ChatCommand(
        "六边形有几条边？", "user-a", "tenant-a", "conversation-a", "scope-a",
    )
    first = asyncio.run(application.handle(command))
    replay = asyncio.run(application.handle(command))

    assert isinstance(first, Completed)
    assert isinstance(replay, Completed)
    assert replay.response_id == first.response_id
    assert replay.response == first.response
    assert tools.calls == []
    assert first.response["agent_outcomes"] == []
    assert first.response["escalated"] is False
    assert not first.response.get("ticket_id")


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
    assert trace["cost"]["conversation_planner_invoked"] is True
    assert len(tools.calls) == 1
    assert tools.calls[0][3]["user_id"] == "user-a"
    assert tools.calls[0][1] == {"order_id": "DP1234"}
    from evaluation.evaluator import EndToEndEvaluator
    scores = EndToEndEvaluator._orchestration_scores(
        SimpleNamespace(**first.response), {"expected_agents": ["order_logistics"]},
    )
    assert scores["coverage_complete"] == 1
    assert scores["task_coverage"] == 1
    assert scores["route_exact_match"] == 1


def test_target_chat_unclear_request_uses_zero_tool_clarification():
    application, tools = _application()
    outcome = asyncio.run(application.handle(ChatCommand(
        "帮帮我", "user-a", "tenant-a", "conversation-a", "request-b",
    )))

    assert isinstance(outcome, Completed)
    assert outcome.response["routing_disposition"] == "clarify"
    assert tools.calls == []


@pytest.mark.parametrize(("message", "route", "tool_calls"), (
    ("查一下订单 DP1234", "direct", 1),
    ("它现在到哪了", "clarify", 0),
))
def test_unavailable_history_preserves_current_input_without_inventing_a_reference(
    message, route, tool_calls,
):
    class ObservedProvider(_ChatPlanningProvider):
        async def plan(self, payload):
            context = payload["conversation_context"]
            assert context["projection_status"] == "UNAVAILABLE"
            assert context["recent_messages"] == []
            return await super().plan(payload)

    application, tools = _application(CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(), ConversationAgent(ObservedProvider()),
    ))
    outcome = asyncio.run(application.handle(ChatCommand(
        message, "user-a", "tenant-a", "conversation-a", "no-history",
    )))
    assert isinstance(outcome, Completed)
    assert outcome.response["routing_disposition"] == route
    assert len(tools.calls) == tool_calls


def test_target_runtime_failure_is_typed_without_exposing_exception_text(monkeypatch):
    application, tools = _application()

    async def fail(*_args, **_kwargs):
        raise RuntimeError("private-provider-credential")

    monkeypatch.setattr(application._turn_runtime, "execute", fail)
    outcome = asyncio.run(application.handle(ChatCommand(
        "查询订单", "user-a", "tenant-a", "conversation-a", "failed-turn",
    )))
    assert isinstance(outcome, Failed)
    assert outcome.code == "target_runtime_failed"
    assert outcome.retryable is False
    assert outcome.correlation_id.startswith("invocation:v1:")
    assert "private-provider-credential" not in repr(outcome)
    assert tools.calls == []


def test_target_chat_publishes_one_typed_interaction_and_resumes_exact_work_item():
    registry = build_default_capability_registry("tenant-a")
    executor = _MissingThenReadExecutor()
    state_store = InMemoryConversationStateStore()
    application = TargetChatApplication(
        manager=TargetConversationManager(
            state_store=state_store,
            registry=registry,
            understanding=_default_understanding(),
            orchestration=OrchestrationRuntime(
                direct_executor=executor,
                domain_workers={},
            ),
        ),
        admission=_Admission(),
        publication=_Publication(),
        bundle_version=registry.bundle_version,
        identity_factory=IdentityFactory(lambda: "generated"),
    )

    first = asyncio.run(application.handle(ChatCommand(
        "查询订单 DP1234", "user-a", "tenant-a", "conversation-input",
        "request-input-1",
    )))

    assert isinstance(first, NeedsInput)
    assert first.kind == "FIELDS"
    state = state_store.load(
        "tenant-a", "user-a", "conversation-input",
    )
    pending = state.pending_interaction
    assert pending is not None
    target = pending.requested_fields[0].target_work_item_id

    stale = asyncio.run(application.handle(ChatCommand(
        "补充核验信息", "user-a", "tenant-a", "conversation-input",
        "request-input-stale",
        interaction_id="another-interaction",
        interaction_version=pending.version,
        interaction_values=((target, "verification_reference", "REF-9"),),
    )))
    assert isinstance(stale, Conflict)
    assert stale.code == "INTERACTION_SIGNAL_CONFLICT"

    second = asyncio.run(application.handle(ChatCommand(
        "补充核验信息", "user-a", "tenant-a", "conversation-input",
        "request-input-2",
        interaction_id=pending.interaction_id,
        interaction_version=pending.version,
        interaction_values=((target, "verification_reference", "REF-9"),),
    )))

    assert isinstance(second, Completed)
    assert "订单已发货" in second.response["response"]
    assert executor.calls == [
        {"order_id": "DP1234"},
        {"order_id": "DP1234", "verification_reference": "REF-9"},
    ]


def test_target_chat_preserves_conversation_provider_failure_as_retryable_failure():
    application, tools = _application(CascadedTargetUnderstanding(
        StateBoundTargetUnderstanding(),
        ConversationAgent(_FailingSemanticProvider()),
    ))

    outcome = asyncio.run(application.handle(ChatCommand(
        "帮我看看 DP1234 走到哪一步了",
        "user-a", "tenant-a", "conversation-a", "request-provider-failure",
    )))

    assert isinstance(outcome, Failed)
    assert outcome.code == "conversation_provider_unavailable"
    assert outcome.retryable is True
    assert tools.calls == []


def test_chat_composition_accessor_has_no_legacy_fallback_authority():
    from api import main

    source = inspect.getsource(main._chat_application)
    assert "_target_run_coordinator" in source
    assert "_durable_chat_coordinator" not in source
    assert "_core_chat_application" not in source


def test_only_target_run_controls_are_exposed_by_http():
    from api import main

    routes = {route.path for route in main.app.routes}
    assert "/invocations/{invocation_key}" in routes
    assert "/chat" in routes
    assert "/agent-runs/{run_id}" not in routes
    assert "/agent-runs/{run_id}/resume" not in routes
    assert not hasattr(main, "_core_chat_application")
    assert not hasattr(main, "_run_store")


def test_target_composition_import_does_not_load_old_runtime():
    import subprocess
    import sys

    subprocess.run([sys.executable, "-c", """
import sys
import infrastructure.target_runtime_composition
assert not {'application.chat_application', 'agents.agent_orchestrator',
            'agents.react_engine', 'agents.run_store'} & sys.modules.keys()
"""], check=True)


def test_http_chat_function_projects_target_completed_response(monkeypatch):
    from api import main
    from core.auth import Principal

    application, _tools = _application()
    monkeypatch.setattr(main, "_target_run_coordinator", application)
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
