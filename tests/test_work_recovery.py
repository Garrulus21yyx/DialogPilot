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


def test_recovery_uses_the_existing_provider_transport_with_its_own_bounded_schema():
    from core.model_policy import ModelProfile, ModelRole
    from tests.framework_structured_stub import models
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    scripted = models({"decisions": [{"work_item_id": "work", "action": "finish"}]},
                      name="submit_work_recovery")
    provider = AnthropicConversationPlanningProvider(scripted,
        model_profile=ModelProfile("test"), synthesis_profile=ModelProfile("test"))
    raw = asyncio.run(provider.recover({"stopped_tasks": [{"work_item_id": "work"}]}))
    assert raw["decisions"][0]["action"] == "finish"
    assert scripted[ModelRole.INTENT].bound_tool_names == ["submit_work_recovery"]
    assert scripted[ModelRole.INTENT].calls == 1


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
        state_with_approval = state.wait_for_approval(PendingApprovalState("approval", 1, "action-stream",
            "write", "order.cancel:v1", "operation", "order:R1", "1", "2099-01-01T00:00:00+00:00",
            checkpoint_thread_id="original-approval-thread"), new_workstream=WorkstreamState(
                "action-stream", "order_logistics", "order.cancel:v1", "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1))
        assert store.compare_and_set(state, state_with_approval)
        first = await manager.handle(identity, TurnObservations("Before confirming, explain my options"))
        pending = first.state_after.pending_interaction
        assert pending is not None
        assert first.state_after.pending_approval == state_with_approval.pending_approval
        assert pending.checkpoint_thread_id != first.state_after.pending_approval.checkpoint_thread_id
        assert conversation_state_from_payload(conversation_state_to_payload(first.state_after)) == first.state_after
        with pytest.raises(DeterministicResolutionError, match="clarification"):
            await manager.handle(_identity("premature-approval"), TurnObservations("Yes",
                approval_decision=True, approval_id="approval"))
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


def test_recovery_question_is_published_and_resumes_after_postgres_reopen(postgres_database_url):
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
