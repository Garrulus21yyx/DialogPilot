"""Stopped work -> conversation decision -> bound reply -> preserved continuation."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
from application.conversation_agent import ConversationAgent
from application.conversation_state import InMemoryConversationStateStore, PendingApprovalState, WorkstreamState, WorkstreamStatus
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations, DeterministicResolutionError
from application.orchestration_runtime import OrchestrationRuntime
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from tests.test_target_persistence_and_manager import _identity, _ResumeAwareUnderstanding


class RecoveryProvider:
    def __init__(self, decision="ask_user"):
        self.decision = decision
        self.payloads = []

    async def recover(self, payload):
        self.payloads.append(payload)
        if self.decision == "error":
            raise ConnectionError("provider unavailable")
        return {"decisions": [{"work_item_id": item["work_item_id"], "action": self.decision,
            **({"question": "Can you provide another reference?"} if self.decision == "ask_user" else {})}
            for item in payload["stopped_tasks"]]}


@pytest.mark.parametrize("has_wait", [False, True])
@pytest.mark.parametrize("roundtrip", [False, True])
def test_planner_resume_choices_match_persisted_waits_not_active_revisions(has_wait, roundtrip):
    from dataclasses import replace
    from application.deterministic_resolution import DeterministicResolution, ResolutionKind
    from tests.test_conversation_agent import Provider
    from tests.test_parameter_acceptance import accepted_work

    state, registry, item, _ = accepted_work(CommandKind.DELEGATE_TASK)
    if not has_wait:
        state = replace(state, pending_interaction=None)
    if roundtrip:
        state = conversation_state_from_payload(conversation_state_to_payload(state))
    provider = Provider({"status": "resolved", "goals": [{"kind": "continue_active_work",
        "revises_control_id": item.control.control_id}]})
    proposal = asyncio.run(ConversationAgent(provider).plan(TurnObservations("Continue"), state,
        DeterministicResolution(ResolutionKind.UNRESOLVED, "TEST", state.fingerprint), registry))
    payload = provider.calls[0]
    assert payload["active_work_controls"]  # Revision remains valid for publication.
    assert bool(payload["resumable_work"]) is has_wait
    assert (proposal.disposition is ProposalDisposition.RESOLVED) is has_wait
    if has_wait:
        assert proposal.commands[0].resumed_work_item == item
        assert payload["resumable_work"][0]["control_id"] == item.control.control_id
    else:
        assert proposal.disposition is ProposalDisposition.INVALID_PROVIDER_OUTPUT


def test_recovery_uses_the_existing_provider_transport_with_its_own_bounded_schema():
    from core.model_policy import ModelProfile, ModelRole
    from tests.framework_structured_stub import models
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    scripted = models({"decisions": [{"work_item_id": "work", "action": "finish"}]},
                      name="submit_work_recovery")
    provider = AnthropicConversationPlanningProvider(scripted,
        model_profile=ModelProfile("test"), synthesis_profile=ModelProfile("test"))
    from infrastructure.target_domain_outcome import ACTION_INTERACTION_CONTRACT
    observed_system = []
    complete = provider._complete
    async def capture(payload, role, system, **kwargs):
        observed_system.append(system)
        return await complete(payload, role, system, **kwargs)
    provider._complete = capture
    raw = asyncio.run(provider.recover({"stopped_tasks": [{"work_item_id": "work"}]}))
    assert raw["decisions"][0]["action"] == "finish"
    assert scripted[ModelRole.INTENT].bound_tool_names == ["submit_work_recovery"]
    assert scripted[ModelRole.INTENT].calls == 1
    assert ACTION_INTERACTION_CONTRACT in observed_system[0]


@pytest.mark.parametrize("effect_status", ["SUCCEEDED", "FAILED", "OUTCOME_UNKNOWN"])
def test_recovery_context_preserves_parameters_receipt_state_and_dependency_evidence(effect_status):
    from dataclasses import replace
    from datetime import datetime, timezone
    from application.agent_result import FactRecord, FactSourceKind, ReceiptRef
    from application.work_recovery import recovery_candidates, recovery_payload
    from tests.test_parameter_acceptance import accepted_work

    _, _, item, _ = accepted_work(CommandKind.DELEGATE_TASK)
    item = replace(item, dependencies=("dependency",))
    timestamp = datetime.now(timezone.utc)
    fact = FactRecord("order:A", "order.current_state", '{"status":"pending"}',
        FactSourceKind.VERIFIED_STATE, "receipt:read", "order_lookup", "v1", timestamp)
    receipt = ReceiptRef("receipt:action", "v1", "op-1", effect_status, "order.change")
    stopped = AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.BLOCKED,
        "STOPPED", "test", facts=(fact,), action_receipts=(receipt,))
    dependency = AgentResult("dependency", item.owner_agent, AgentResultStatus.SUCCEEDED,
        "DONE", "test", facts=(fact,), action_receipts=(receipt,))
    dependency_item = replace(item, work_item_id="dependency", dependencies=())
    # A retained result may reuse a local ID from another invocation/control.
    historical = replace(item, control=replace(item.control, control_id="historical-control"))
    historical_result = replace(dependency, work_item_id=item.work_item_id)
    from tests.test_write_workflow import _item as write_item
    prepared = write_item()
    approval_item = replace(item, work_item_id="approval", owner_agent=prepared.owner_agent,
        control=replace(item.control, control_id="approval-control"))
    waiting = AgentResult("approval", prepared.owner_agent, AgentResultStatus.WAITING_APPROVAL,
        "PREPARED", "test", pending_action=prepared)
    plan, board = SimpleNamespace(items=(item,)), SimpleNamespace(results=(stopped, dependency),
        outcome_items=((item, stopped), (dependency_item, dependency), (historical, historical_result),
                       (approval_item, waiting)))
    candidates = recovery_candidates(plan, board)
    if effect_status == "OUTCOME_UNKNOWN":
        assert not candidates  # Reconciliation cannot be turned into user input.
        return
    payload = recovery_payload(plan, board, candidates, current_message="Help",
        conversation_context={"recent_messages": ["Only inspect; do not change it"]})
    projected = json.loads(json.dumps(payload))  # Actual provider transport must accept it.
    task, = projected["stopped_tasks"]
    assert task["accepted_arguments"] == {"order_id": "DP1111"}
    assert task["allowed_capabilities"]["tools"] == list(item.allowed_tools)
    assert task["dependencies"] == ["dependency"]
    assert task["action_receipts"][0]["effect_status"] == effect_status
    assert "completed_receipt_ids" not in task
    assert task["retained_facts"][0]["subject_ref"] == "order:A"
    assert task["retained_facts"][0]["observed_at"] == timestamp.isoformat()
    assert task["retained_facts"][0]["source_kind"] == "VERIFIED_STATE"
    assert projected["other_outcomes"][0]["action_receipts"] == task["action_receipts"]
    assert projected["other_outcomes"][1]["control"]["control_id"] == "historical-control"
    assert projected["other_outcomes"][1]["work_item_id"] == item.work_item_id
    pending = projected["other_outcomes"][2]["pending_action"]
    assert pending["action_ref"] == prepared.action_ref
    assert pending["status"] == "AWAITING_APPROVAL_NOT_EXECUTED"


def _setup(worker, *, provider=None, store=None, checkpointer=None):
    registry = build_default_capability_registry("tenant-target")
    store = store or InMemoryConversationStateStore()
    orchestration = OrchestrationRuntime(direct_executor=worker,
        domain_workers={"product_technical": worker, "order_logistics": worker},
        checkpointer=checkpointer or InMemorySaver(serde=target_checkpoint_serializer()))
    manager = TargetConversationManager(state_store=store, registry=registry,
        conversation_agent=ConversationAgent(provider) if provider else None,
        understanding=_ResumeAwareUnderstanding(TurnProposal(ProposalDisposition.RESOLVED, (
            CommandProposal("product", CommandKind.DELEGATE_TASK, "product_technical", "Find the requested product"),
            CommandProposal("order", CommandKind.DELEGATE_TASK, "order_logistics", "Read order status")), "TEST")),
        orchestration=orchestration)
    return manager, orchestration, store


@pytest.mark.parametrize("reject_again", [False, True])
@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_reply_rejection_never_reexecutes_work_and_can_resume_assembly(reject_again, backend, request):
    from hashlib import sha256
    from application.response_assembly import AssembledResponse, ResponseAssemblyMode
    from application.turn_runtime import TurnRuntime, InteractionAssemblyUnavailable

    async def run(saver=None, state_store=None):
        from datetime import datetime, timezone
        from application.agent_result import FactRecord, FactSourceKind
        fact = FactRecord("order:A", "order.current_state", '{"status":"shipped"}',
            FactSourceKind.VERIFIED_STATE, "receipt:read", "order_lookup", "v1", datetime.now(timezone.utc))
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item.owner_agent)
            assert not item.continuation_of
            if item.owner_agent == "product_technical":
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Which product do you mean?"),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
                "DONE", "test", candidate_response="Found",
                facts=(fact,) if item.owner_agent == "order_logistics" else ())

        manager, _, store = _setup(worker, checkpointer=saver, store=state_store)
        from uuid import uuid4
        identity = _identity(f"internal-repair-{uuid4().hex}")
        class Assembler:
            fallback_locale = "en"
            calls = 0
            async def assemble(self, board, *, requested_inputs, **kwargs):
                self.calls += 1
                stored = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
                assert stored.pending_interaction is None  # Never publish an invalid wait.
                assert stored.pending_approval is None
                assert requested_inputs
                if self.calls == 1 or reject_again:
                    return AssembledResponse("", ResponseAssemblyMode.TEMPLATE, (), False,
                        "REJECT", "INCOMPLETE")
                return AssembledResponse("Found", ResponseAssemblyMode.TEMPLATE, (), False,
                    "PASS", "VALID", verified_text_sha256=sha256(b"Found").hexdigest())

        checkpoints = saver or InMemorySaver(serde=target_checkpoint_serializer())
        assembler = Assembler()
        runtime = TurnRuntime(manager=manager, response_assembler=assembler, checkpointer=checkpoints)
        observations = TurnObservations("Find the product and read my order")
        with pytest.raises(InteractionAssemblyUnavailable):
            await runtime.execute(identity, observations)
        reopened = TurnRuntime(manager=manager, response_assembler=assembler, checkpointer=checkpoints)
        if reject_again:
            with pytest.raises(InteractionAssemblyUnavailable):
                await reopened.execute(identity, observations)
        else:
            result = await reopened.execute(identity, observations)
            assert result.managed.state_after.pending_interaction is not None
            await reopened.execute(identity, observations)  # Completed graph replay is side-effect free.
        assert calls.count("product_technical") == 1
        assert calls.count("order_logistics") == 1
        assert (store.load(identity.tenant_id, identity.user_id, identity.conversation_id).pending_interaction is None) is reject_again
    if backend == "memory":
        asyncio.run(run())
    else:
        from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
        from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresMigrationRunner
        from infrastructure.postgres_target_runtime import PostgresConversationStateStore
        url = request.getfixturevalue("fresh_postgres_database_url")
        async def persisted():
            PostgresMigrationRunner(url).upgrade()
            pool = PostgresPool(PostgresPoolConfig(url, min_size=1, max_size=3))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                    await run(saver, PostgresConversationStateStore(pool))
            finally:
                pool.close()
        asyncio.run(persisted())


@pytest.mark.parametrize("status", [AgentResultStatus.BLOCKED, AgentResultStatus.RETRYABLE_FAILURE,
                                    AgentResultStatus.TERMINAL_FAILURE])
@pytest.mark.parametrize("decision", ["ask_user", "finish", "error"])
def test_stopped_work_handback_preserves_independent_success_and_feedback(status, decision):
    async def run():
        calls = []
        history = ({"type": "tool", "data": {"name": "catalog_search", "tool_call_id": "call1",
            "content": json.dumps({"error": "reference is ambiguous", "status": "rejected"})}},)

        async def worker(context):
            item = context.work_item
            calls.append(item.owner_agent)
            if item.owner_agent == "product_technical" and not item.continuation_of:
                return AgentResult(item.work_item_id, item.owner_agent, status, "LOOKUP_STOPPED", "test",
                    retryable=status is AgentResultStatus.RETRYABLE_FAILURE, working_messages=history,
                    execution_feedback=({"tool": "catalog_search", "call_id": "call1",
                        "error": "reference is ambiguous", "status": "rejected"},))
            if item.continuation_of:
                assert context.working_messages == history
                assert context.current_message == "Try reference B instead"
                assert "approval_binding" not in context.trusted_context
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
                               "DONE", "test", candidate_response="Found")

        provider = RecoveryProvider(decision)
        manager, orchestration, store = _setup(worker, provider=provider)
        first = await manager.handle(_identity("stopped"), TurnObservations("Find the product and read my order"))
        assert len(provider.payloads) == 1
        feedback = provider.payloads[0]["stopped_tasks"][0]["execution_feedback"]
        assert feedback[0]["error"] == "reference is ambiguous"
        assert first.board.results[0].status is status  # Recovery does not rewrite execution truth.
        assert first.board.results[1].status is AgentResultStatus.SUCCEEDED
        pending = first.state_after.pending_interaction
        if decision != "ask_user":
            assert pending is None
            snapshot = await orchestration.graph.aget_state({"configurable": {"thread_id": first.checkpoint_thread_id}})
            assert not snapshot.next
            assert len(calls) == 2  # No invisible whole-task retries.
            return
        assert pending.checkpoint_thread_id == first.checkpoint_thread_id
        assert len(pending.suspended_work_items) == 1
        assert first.interaction_questions[0].question_hint == "Can you provide another reference?"
        # Reopen both application and graph with existing persisted state.
        restored = conversation_state_from_payload(conversation_state_to_payload(first.state_after))
        assert restored == first.state_after
        reopened, _, _ = _setup(worker, provider=provider, store=store,
                                checkpointer=orchestration._checkpointer)
        second = await reopened.handle(_identity("reply"), TurnObservations("Try reference B instead",
            interaction_id=pending.interaction_id, interaction_version=pending.version))
        assert second.checkpoint_thread_id == first.checkpoint_thread_id
        assert second.state_after.pending_interaction is None
        assert calls.count("order_logistics") == 1
        assert calls.count("product_technical") == 2
        assert len(provider.payloads) == 1
    asyncio.run(run())


def test_approval_and_clarification_have_distinct_checkpoints_and_signals():
    async def run():
        from dataclasses import replace
        from application.conversation_state import WorkControlState, WorkControlStatus
        from application.work_item import WorkControlBinding
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item.owner_agent)
            if not item.continuation_of and item.owner_agent == "product_technical":
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Which option?"),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
                               "DONE", "test", candidate_response="Found")
        manager, _, store = _setup(worker)
        identity = _identity("clarify")
        state = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
        bound = replace(state, work_controls=(WorkControlState("original-goal", 1, "original-work",
            "original-invocation", "order_logistics", "Cancel the order", WorkControlStatus.ACTIVE, 0),))
        state_with_approval = bound.wait_for_approval(PendingApprovalState("approval", 1, "action-stream",
            "write", "order.cancel:v1", "operation", "order:R1", "1", "2099-01-01T00:00:00+00:00",
            checkpoint_thread_id="original-approval-thread", control=WorkControlBinding("original-goal", 1)), new_workstream=WorkstreamState(
                "action-stream", "order_logistics", "order.cancel:v1", "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1))
        assert store.compare_and_set(state, state_with_approval)
        first = await manager.handle(identity, TurnObservations("Before confirming, explain my options"))
        pending = first.state_after.pending_interaction
        assert pending is not None
        assert first.state_after.pending_approval == state_with_approval.pending_approval
        assert pending.checkpoint_thread_id != first.state_after.pending_approval.checkpoint_thread_id
        assert conversation_state_from_payload(conversation_state_to_payload(first.state_after)) == first.state_after
        # Independent waits may coexist; only an incorrect signal binding is
        # rejected before planning, not the mere presence of a clarification.
        with pytest.raises(DeterministicResolutionError, match="approval"):
            await manager.handle(_identity("stale-approval"), TurnObservations("",
                approval_decision=True, approval_id="stale-approval"))
        second = await manager.handle(_identity("clarification-answer"), TurnObservations("Option B",
            interaction_id=pending.interaction_id, interaction_version=pending.version))
        assert second.state_after.pending_interaction is None
        assert second.state_after.pending_approval == state_with_approval.pending_approval
        assert second.checkpoint_thread_id == pending.checkpoint_thread_id
        assert len(calls) == 3
    asyncio.run(run())


@pytest.mark.parametrize("status", [AgentResultStatus.SUCCEEDED, AgentResultStatus.CANCELLED,
    AgentResultStatus.SUPERSEDED, AgentResultStatus.RECONCILING])
def test_recovery_does_not_restart_terminal_control_or_unknown_writes(status):
    from application.work_recovery import recovery_candidates
    from tests.test_target_framework_agent import _item
    item = _item()
    result = AgentResult(item.work_item_id, item.owner_agent, status, "TEST", "test")
    assert recovery_candidates(SimpleNamespace(items=(item,)), SimpleNamespace(results=(result,))) == ()


@pytest.mark.parametrize("decisions", [[],
    [{"work_item_id": "other", "action": "ask_user", "question": "Which one?"}],
    [{"work_item_id": "work", "action": "execute_refund"}],
    [{"work_item_id": "work", "action": "finish", "question": "Which one?"}],
    [{"work_item_id": "work", "action": "finish"}] * 2])
def test_recovery_decision_algebra_rejects_missing_duplicate_or_unauthorized_targets(decisions):
    from application.work_recovery import recovery_questions
    from jsonschema import ValidationError
    with pytest.raises((ValueError, ValidationError)):
        recovery_questions({"decisions": decisions}, (SimpleNamespace(work_item_id="work"),))


def test_recovery_question_is_published_and_resumes_after_postgres_reopen(fresh_postgres_database_url):
    postgres_database_url = fresh_postgres_database_url
    from application.chat_contracts import ChatCommand, NeedsInput, Completed
    from application.target_chat_application import TargetChatApplication
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
    from infrastructure.postgres_target_runtime import PostgresConversationStateStore
    from tests.test_target_chat_cutover import _Admission, _Publication

    async def run():
        PostgresMigrationRunner(postgres_database_url).upgrade()
        calls = []
        history = ({"type": "human", "data": {"content": json.dumps({
            "execution_feedback": {"reason_code": "NO_PROGRESS", "completed": ["lookup"]}})}},)
        async def worker(context):
            item = context.work_item
            calls.append(item.owner_agent)
            if item.owner_agent == "product_technical" and not item.continuation_of:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.BLOCKED,
                    "NO_PROGRESS", "test", working_messages=history)
            if item.continuation_of:
                assert context.working_messages == history
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
                               "DONE", "test", candidate_response="Found")
        class Publication(_Publication):
            questions = []
            def publish_interaction(self, *args, **kwargs):
                self.questions.append(kwargs["challenge"])
                return super().publish_interaction(*args, **kwargs)
        publication, admission, provider = Publication(), _Admission(), RecoveryProvider()
        pending = None
        for phase in ("first", "reopen"):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=3))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=True) as saver:
                    store = PostgresConversationStateStore(pool)
                    manager, _, _ = _setup(worker, provider=provider, store=store, checkpointer=saver)
                    from tests.test_knowledge_answer_boundary import Verifier
                    class Author:
                        async def compose(self, payload):
                            return "\n".join([('Can you provide another reference?')])
                    app = TargetChatApplication(manager=manager, admission=admission, publication=publication,
                        bundle_version="test", turn_runtime=TurnRuntime(manager,
                            ResponseAssembler(Author(), knowledge_verifier=Verifier(True)), checkpointer=saver))
                    extra = {} if pending is None else {"interaction_id": pending.interaction_id,
                                                         "interaction_version": pending.version}
                    result = await app.handle(ChatCommand("Find product" if pending is None else "Use reference B",
                        "user-target", tenant_id="tenant-target", conv_id="conversation-target", request_id=phase, **extra))
                    state = store.load("tenant-target", "user-target", "conversation-target")
                    if phase == "first":
                        assert isinstance(result, NeedsInput), result
                        pending = state.pending_interaction
                        assert publication.questions == ["Can you provide another reference?"]
                    else:
                        assert isinstance(result, Completed), result
                        assert state.pending_interaction is None
            finally:
                pool.close()
        assert calls == ["product_technical", "order_logistics", "product_technical"]
        assert len(provider.payloads) == 1
    asyncio.run(run())
