import asyncio
import inspect
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.tool_manager import ToolResult

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
    MissingInputSpec,
)
from application.chat_contracts import ChatCommand, Completed, Conflict, Failed, NeedsInput, Reconciling
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
        execution_stages=(),
        knowledge_evidence=(),
    ):
        key = str(identity.invocation_key)
        published = PublishedTargetResponse(f"response:{key}", 1, "selected")
        body = dict(public_response)
        body.update({
            "response_id": published.response_id,
            "response_seq": published.response_seq,
            "delivery_status": published.delivery_status,
        })
        self.responses[key] = (Failed(body["code"], False, body["correlation_id"],
            response_text, execution_stages, response_id=published.response_id)
            if body.get("outcome") == "failed" else
            Reconciling(body["workflow_run_id"], body, body["next_poll_after"], stages=execution_stages)
            if body.get("outcome") == "reconciling" else
            Completed(published.response_id, body, stages=execution_stages))
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
        expected_work_controls=(),
        related_signals=(),
        execution_stages=(), knowledge_evidence=(),
    ):
        key = str(identity.invocation_key)
        published = PublishedTargetResponse(f"interaction:{key}", 1, "selected")
        self.responses[key] = NeedsInput(
            str(identity.workflow_run_id),
            signal_id,
            str(resume_schema.get("interaction_kind", "APPROVAL")),
            expires_at,
            published.response_id,
            stages=execution_stages,
        )
        self.related_signals = (*getattr(self, "related_signals", ()), *related_signals)
        self.interaction_payloads = (*getattr(self, "interaction_payloads", ()), resume_schema)
        return published

    def has_interaction(self, identity, *, signal_id, signal_version):
        return (signal_id, signal_version) in getattr(self, "related_signals", ()) or any(
            isinstance(response, NeedsInput) and response.signal_id == signal_id
            for response in self.responses.values())


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
                '{"order_id":"DP1234","status":"shipped"}',
                FactSourceKind.VERIFIED_STATE,
                "receipt:order-read",
                "order_lookup",
                "order-view-v2",
                datetime.now(timezone.utc),
            ),),
            candidate_response="订单已发货。",
        )


class _ToolManager:
    def __init__(self):
        self.calls = []

    async def execute_for_agent(
        self, name, params, *, agent_type, context, approved=False, call_id=None,
        allowed_tool_ids=None,
    ):
        self.calls.append((name, dict(params), agent_type, dict(context)))
        return ToolResult(
            success=True,
            tool_name=name,
            data={"order_id": params["order_id"], "status": "shipped"},
            authority="order.current_state",
            receipt_id="",
            call_id=call_id,
            output_schema_version="order-view-v2",
            output_for_model="订单 DP1234 已发货。",
            status="success",
        )


def _application(understanding=None):
    tools = _ToolManager()
    registry = build_default_capability_registry("tenant-a")
    executor = TargetToolExecutor(tools, registry=registry)
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


@pytest.mark.parametrize("retained_count", [1, 3])
def test_public_outcomes_preserve_control_when_local_ids_repeat(monkeypatch, retained_count):
    from application.work_item import WorkControlBinding

    original_execute = OrchestrationRuntime.execute

    async def execute_with_history(self, *args, **kwargs):
        board = await original_execute(self, *args, **kwargs)
        item, result = board.outcome_items[0]
        retained = tuple((replace(item, control=WorkControlBinding(f"prior-{index}", 1)), result)
                         for index in range(retained_count))
        return replace(board, retained_outcomes=retained)

    monkeypatch.setattr(OrchestrationRuntime, "execute", execute_with_history)
    application, _ = _application()
    outcome = asyncio.run(application.handle(ChatCommand(
        "查一下订单 DP1234 物流", "user-a", "tenant-a", "conversation-a", "request-a")))
    assert isinstance(outcome, Completed)
    results = outcome.response["agent_outcomes"]
    assert len(results) == retained_count + 1
    assert len({item["work_item_id"] for item in results}) == 1
    assert len({item["control"]["control_id"] for item in results}) == retained_count + 1
    assert {item["control"]["control_id"] for item in results[:-1]} == {
        f"prior-{index}" for index in range(retained_count)}


def test_one_publication_exposes_both_approval_and_field_bindings():
    from application.agent_result import RequestedField
    from application.conversation_state import PendingInteractionState
    from tests.test_approval_revision_lifecycle import pending_state
    from jsonschema import validate
    from application.response_assembly import ResponseAssembler
    from application.agent_result import MissingInputSpec
    from tests.test_response_assembly import _board, _result
    from tests.test_knowledge_answer_boundary import Verifier
    state, origin, other = pending_state(False)
    state = replace(state, pending_approval=replace(state.pending_approval,
        checkpoint_thread_id="thread", suspended_work_items=(origin,)))
    state = state.wait_for_interaction(PendingInteractionState("fields", 1,
        (RequestedField("reply", other.work_item_id, "string"),), (), (other,), "thread"))
    application, _ = _application()
    class Composer:
        async def compose(self, payload):
            assert payload["evidence"]["pending_actions"]
            assert payload["evidence"]["requested_inputs"]
            assert any("approval" in requirement.lower() for requirement in payload["response_requirements"])
            return "Approve the operation? Also, which option?"
    assembler = ResponseAssembler(Composer(), knowledge_verifier=Verifier(True), fallback_locale="en")
    async def execute(*args, **kwargs):
        assembly = await assembler.assemble(_board(_result(origin.work_item_id, origin.owner_agent)),
            current_message="Proceed", pending_approval=state.pending_approval,
            requested_inputs=(MissingInputSpec("reply", other.work_item_id, "INPUT", "string", "Which option?"),))
        return SimpleNamespace(managed=SimpleNamespace(plan=SimpleNamespace(response_text=None), state_before=replace(state,
            pending_interaction=None, pending_approval=None), state_after=state),
            assembled=assembly)
    application._turn_runtime.execute = execute
    command = ChatCommand("Proceed", "user-a", "tenant-a", "conversation-a", "request-a")
    response = asyncio.run(application.handle(command))
    assert isinstance(response, NeedsInput) and response.kind == "COMPOUND"
    assert len(application._publication.responses) == 1
    schema, = application._publication.interaction_payloads
    assert application._publication.has_interaction(None, signal_id="fields", signal_version=1)
    assert application._publication.has_interaction(None, signal_id="approval", signal_version=1)
    validate({"approval_id": "approval", "approved": True}, schema)
    values = {"interaction_id": "fields", "interaction_version": 1, "interaction_values": [{
        "target_work_item_id": other.work_item_id, "field_name": "reply", "value": "blue"}]}
    validate(values, schema)
    validate({**values, "approval_id": "approval", "approved": False}, schema)


@pytest.mark.parametrize("unknown_count", [1, 3])
def test_unknown_actions_publish_independent_results_and_keep_their_own_references(monkeypatch, unknown_count):
    from application.result_board import ResultBoard
    from tests.test_write_workflow import _item as write_item
    original_execute = OrchestrationRuntime.execute

    async def execute_with_unknown_history(self, *args, **kwargs):
        board = await original_execute(self, *args, **kwargs)
        retained = []
        for index in range(unknown_count):
            item = replace(write_item(f"operation-{index}"), work_item_id=f"write-{index}",
                           approval_binding=f"approval-{index}")
            result = AgentResult(item.work_item_id, item.owner_agent,
                AgentResultStatus.RECONCILING, "OUTCOME_UNKNOWN", "test")
            retained.append((item, result))
        from application.work_item import WorkPlan
        return ResultBoard().evaluate(WorkPlan(board.work_items, board.work_items[0].work_item_id),
            board.results, retained_outcomes=tuple(retained))

    monkeypatch.setattr(OrchestrationRuntime, "execute", execute_with_unknown_history)
    application, tools = _application()
    command = ChatCommand("查一下订单 DP1234 物流", "user-a", "tenant-a", "conversation-a", "unknown-history")
    outcome = asyncio.run(application.handle(command))
    assert isinstance(outcome, Reconciling)
    assert "DP1234" in outcome.public_status["response"]
    assert not outcome.public_status["task_completed"]
    assert {item["approval_id"] for item in outcome.public_status["reconciling_actions"]} == {
        f"approval-{index}" for index in range(unknown_count)}
    assert len(application._publication.responses) == 1
    calls = len(tools.calls)
    assert asyncio.run(application.handle(command)) == outcome
    assert len(tools.calls) == calls


def test_chat_request_accepts_typed_decision_without_fabricated_user_text():
    from api.main import ChatRequest
    from pydantic import ValidationError
    assert ChatRequest(approval_id="approval", approved=True).message == ""
    with pytest.raises(ValidationError):
        ChatRequest()


class _FailingSemanticProvider:
    version = "failing-semantic-provider-test-v1"

    async def plan(self, payload):
        from core.framework_models import ModelInvocationError
        raise ModelInvocationError("conversation_plan", TimeoutError("provider unavailable"))


class _ChatPlanningProvider:
    version = "chat-planning-provider-test-v1"

    async def plan(self, payload):
        message = str(payload["message"])
        if "DP1234" in message:
            return {
                "status": "resolved",
                "goals": [{"kind": "order_status", "order_id": "DP1234",
                    "order_id_source_ref": next(c["source_ref"]
                        for field in payload["entity_bindings"] for c in field["candidates"]
                        if c["value"] == "DP1234")}],
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
    assert first.response["verification_status"] == "not_checked"
    assert first.response["verified"] is False
    assert first.response["task_completed"] is False
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
    assert first.response["verified"] is False
    assert first.response["grounded"] is False
    assert first.response["verification_status"] == "not_checked"
    assert first.response["task_completed"] is True
    assert "已发货" in first.response["response"]
    trace = first.response["evaluation_trace"]
    assert tuple(trace) == (
        "schema_version", "trigger", "artifact", "consumption",
        "state_side_effect", "outcome", "cost",
    )
    assert trace["artifact"]["route_mode"] == "DIRECT"
    assert trace["consumption"]["work_items"][0]["control_mode"] == "DIRECT"
    assert first.response["agent_outcomes"][0]["control"] == trace["consumption"]["work_items"][0]["control"]
    fact, = trace["consumption"]["facts"]
    assert fact == {
        "requirement_id": "order.current_state",
        "source_kind": "VERIFIED_STATE",
        "source_ref": f"{trace['consumption']['work_items'][0]['work_item_id']}:1:order_lookup",
        "producer_id": "order_lookup",
        "producer_version": "order-view-v2",
    }
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
    from application.public_chat_contract import project_chat_outcome
    assert "private-provider-credential" not in repr(project_chat_outcome(outcome))
    assert outcome.stages[0].detail["exception_chain"][0]["type"] == "RuntimeError"
    assert tools.calls == []


@pytest.mark.parametrize("internal", [False, True])
def test_plan_rejection_and_trusted_state_failure_keep_distinct_outcomes(monkeypatch, internal):
    from application.turn_planning import TurnPlanningError, PlanningInvariantError
    from application.public_chat_contract import project_chat_outcome
    application, tools = _application()

    async def fail(*_args, **_kwargs):
        error = PlanningInvariantError if internal else TurnPlanningError
        raise error("diagnostic-only plan detail")

    monkeypatch.setattr(application._turn_runtime, "execute", fail)
    outcome = asyncio.run(application.handle(ChatCommand(
        "查询订单", "user-a", "tenant-a", "conversation-a", "rejected-plan")))
    assert isinstance(outcome, Failed)
    assert outcome.code == ("planning_invariant_failed" if internal else "planning_candidate_rejected")
    assert outcome.retryable is False
    assert outcome.stages[0].detail["exception_chain"][0]["type"] == (
        "PlanningInvariantError" if internal else "TurnPlanningError")
    assert "diagnostic-only plan detail" not in str(project_chat_outcome(outcome))
    assert tools.calls == []


def test_framework_control_signal_crosses_application_without_failure_conversion(monkeypatch):
    from langgraph.errors import GraphInterrupt
    application, tools = _application()

    async def interrupt(*args, **kwargs):
        raise GraphInterrupt(())

    monkeypatch.setattr(application._turn_runtime, "execute", interrupt)
    with pytest.raises(GraphInterrupt):
        asyncio.run(application.handle(ChatCommand("查询订单", "user-a", "tenant-a",
            "conversation-a", "interrupted")))
    assert not tools.calls
    assert not application._publication.responses


def test_failure_publication_remains_a_failure_on_replay():
    application, tools = _application()
    identity = application.identity_for(ChatCommand("查询订单", "user-a", "tenant-a",
        "conversation-a", "failed-notice"))
    failed = Failed("planning_candidate_rejected", False, str(identity.invocation_key))
    published = application.publish_failure(identity, failed)
    assert isinstance(published, Failed)
    assert published.response_id
    assert application.completed(identity) == published
    assert not tools.calls
    with pytest.raises(ValueError, match="retryable"):
        application.publish_failure(identity, replace(failed, retryable=True))


@pytest.mark.parametrize("fail_first", [False, True])
def test_target_chat_publishes_one_typed_interaction_and_resumes_exact_work_item(fail_first):
    from application.response_assembly import ResponseAssembler
    from tests.test_knowledge_answer_boundary import Verifier
    registry = build_default_capability_registry("tenant-a")
    executor = _MissingThenReadExecutor()
    state_store = InMemoryConversationStateStore()
    class Composer:
        calls = 0
        async def compose(self, payload):
            self.calls += 1
            if fail_first and self.calls == 1:
                from core.framework_models import ModelInvocationError
                raise ModelInvocationError("compose", TimeoutError("injected failure"))
            question = bool(payload['evidence']['requested_inputs'])
            if question:
                return "\n".join([('请提供订单核验信息。')])
            return "\n".join([('请提供订单核验信息。' if question else '订单 DP1234 当前状态为已发货。')])
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
        response_assembler=ResponseAssembler(Composer(), knowledge_verifier=Verifier(True)),
        identity_factory=IdentityFactory(lambda: "generated"),
    )

    first = asyncio.run(application.handle(ChatCommand(
        "查询订单 DP1234", "user-a", "tenant-a", "conversation-input",
        "request-input-1",
    )))

    if fail_first:
        assert isinstance(first, Completed)
        assert first.response["execution"] == "WAITING"
        assert first.response["interaction_presentation"] == "UNAVAILABLE"
        assert not first.response["task_completed"]
        assert not first.response["verified"]
        assert first.stages
    else:
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
    assert "订单 DP1234 当前状态为已发货" in second.response["response"]
    assert executor.calls == [
        {"order_id": "DP1234"},
        {"order_id": "DP1234", "verification_reference": "REF-9"},
    ]


def test_target_chat_without_checkpoint_does_not_advertise_provider_retry():
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
    assert outcome.retryable is False
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
    assert response.verified is False
    assert response.task_completed is True
    assert response.evaluation_trace["outcome"]["verifier_status"] == "NOT_CHECKED"


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


@pytest.mark.parametrize('reason,mode,composed', [
    ('ANSWER_SUPPORT_CHECKED', 'TEMPLATE', False),
    ('KNOWLEDGE_SUPPORT_CHECKED', 'TEMPLATE', False),
    ('SINGLE_VERIFIED_RESULT', 'PASS_THROUGH', False),
    ('COMPOSED', 'CONVERSATION_COMPOSE', True),
])
def test_old_checked_checkpoint_without_answer_binding_cannot_publish(reason, mode, composed):
    from dataclasses import replace
    from application.response_assembly import ResponseAssemblyMode
    application,_tools=_application()
    delegate=application._turn_runtime
    class OldCheckpoint:
        async def execute(self,*args,**kwargs):
            result=await delegate.execute(*args,**kwargs)
            return replace(result,assembled=replace(result.assembled,
                text='保证退款。', mode=ResponseAssemblyMode(mode), composer_used=composed,
                verification_reason=reason,verified_text_sha256=''))
    application._turn_runtime=OldCheckpoint()
    result=asyncio.run(application.handle(ChatCommand(
        '查订单 DP1234','user-a','tenant-a','old-checkpoint','old-checkpoint-request')))
    assert isinstance(result, Failed)
    assert result.code=='target_verified_answer_changed'
    assert not application._publication.responses
