"""Execution-owned results -> deterministic wait binding -> one publication."""
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


@pytest.mark.parametrize("status", [AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL,
    AgentResultStatus.BLOCKED, AgentResultStatus.NEEDS_EVIDENCE, AgentResultStatus.RETRYABLE_FAILURE,
    AgentResultStatus.TERMINAL_FAILURE, AgentResultStatus.CANCELLED, AgentResultStatus.SUPERSEDED])
@pytest.mark.parametrize("successful_count", [1, 3])
@pytest.mark.parametrize("compose_fails", [False, True])
def test_original_task_outcomes_drive_partial_delivery_without_a_second_planner(status, successful_count, compose_fails):
    from datetime import datetime, timezone
    from application.agent_result import FactRecord, FactSourceKind, EvidenceRequest
    from application.chat_contracts import ChatCommand, Completed
    from application.target_chat_application import TargetChatApplication
    from application.response_assembly import ResponseAssembler
    from application.turn_runtime import TurnRuntime
    from tests.test_target_chat_cutover import _Admission, _Publication
    from tests.test_knowledge_answer_boundary import Verifier
    calls, authored = [], []
    async def worker(context):
        item = context.work_item
        calls.append(item.work_item_id)
        failed = item.owner_agent == "product_technical"
        facts = () if failed else (FactRecord("order:" + item.work_item_id, "order.current_state",
            '{"order_id":"DP1234","status":"shipped"}', FactSourceKind.VERIFIED_STATE, "receipt:" + item.work_item_id,
            "order_lookup", "v1", datetime.now(timezone.utc)),)
        return AgentResult(item.work_item_id, item.owner_agent, status if failed else AgentResultStatus.SUCCEEDED,
            "STOPPED" if failed else "DONE", "test", facts=facts,
            execution_feedback=({"stage": "tool", "code": "UNAVAILABLE"},) if failed else (),
            requested_evidence=(EvidenceRequest("product.model", item.work_item_id, ("catalog",)),)
                if failed and status is AgentResultStatus.NEEDS_EVIDENCE else (),
            retryable=failed and status is AgentResultStatus.RETRYABLE_FAILURE)
    class Author:
        async def compose(self, payload):
            authored.append(payload)
            if compose_fails:
                raise ConnectionError("author unavailable")
            return "The order is shipped. The product task result is " + status.value + "."
    async def run():
        manager, work_runtime, _ = _setup(worker)
        manager._understanding = _ResumeAwareUnderstanding(TurnProposal(ProposalDisposition.RESOLVED,
            (CommandProposal("product", CommandKind.DELEGATE_TASK, "product_technical", "Product goal"),
             *(CommandProposal("order-" + str(i), CommandKind.DELEGATE_TASK, "order_logistics", "Order goal")
               for i in range(successful_count))), "TEST"))
        publication = _Publication()
        turn = TurnRuntime(manager, ResponseAssembler(Author(), knowledge_verifier=Verifier(True), fallback_locale="en"),
            checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
        app = TargetChatApplication(manager=manager, admission=_Admission(), publication=publication,
            bundle_version="test", turn_runtime=turn)
        command = ChatCommand("Both goals", "user-target", "tenant-target", "conversation-target", "one")
        outcome = await app.handle(command)
        assert isinstance(outcome, Completed), outcome
        assert "shipped" in outcome.response["response"].lower()
        assert outcome.response["task_completed"] is (status is AgentResultStatus.SUCCEEDED)
        assert len(calls) == successful_count + 1
        assert len(publication.responses) == 1
        assert len(authored) == 1
        evidence = authored[0]["evidence"]
        assert [row["observed_segment"]["status"] for row in evidence["outcomes"]] == [status.value] + ["SUCCEEDED"] * successful_count
        identity = app.identity_for(command)
        snapshot = await turn.graph.aget_state({"configurable": {"thread_id": "turn:" + str(identity.invocation_key)}})
        managed = snapshot.values["managed"]
        assert managed.state_after.pending_interaction is None
        assert managed.board.results[0].status is status
        assert not (await work_runtime.graph.aget_state({"configurable": {"thread_id": managed.checkpoint_thread_id}})).next
        replay = await app.handle(command)
        assert replay.response_id == outcome.response_id
        assert len(calls) == successful_count + 1 and len(authored) == 1
    asyncio.run(run())


@pytest.mark.parametrize("additional_goals", [1, 3])
def test_new_independent_questions_extend_one_wait_without_rerunning_old_work(additional_goals):
    async def run():
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item)
            if not item.continuation_of:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "INPUT", "test", missing_inputs=(MissingInputSpec("reply", item.work_item_id,
                        "INPUT", "string", "Choose for " + item.objective),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, "DONE", "test")
        manager, runtime, store = _setup(worker)
        first = None
        for index in range(additional_goals + 1):
            manager._understanding.initial = TurnProposal(ProposalDisposition.RESOLVED,
                (CommandProposal("same-local-id", CommandKind.DELEGATE_TASK, "product_technical", "Goal " + str(index)),), "TEST")
            result = await manager.handle(_identity("goal-" + str(index)), TurnObservations("New independent goal"))
            pending = result.state_after.pending_interaction
            first = first or pending
            assert pending.interaction_id == first.interaction_id
            assert pending.version == index + 1
            assert pending.checkpoint_thread_id == first.checkpoint_thread_id
            assert len(pending.requested_fields) == index + 1
            assert len(calls) == index + 1
            assert len({item.work_item_id for item in calls}) == len(calls)
        with pytest.raises(DeterministicResolutionError):
            await manager.prepare(_identity("stale"), TurnObservations("Old answer",
                interaction_id=first.interaction_id, interaction_version=first.version))
        values = tuple((field.target_work_item_id, field.field_name, "selected") for field in pending.requested_fields)
        completed = await manager.handle(_identity("answers"), TurnObservations("", interaction_id=pending.interaction_id,
            interaction_version=pending.version, interaction_values=values))
        assert completed.state_after.pending_interaction is None
        assert completed.board.task_completed
        assert len(calls) == 2 * (additional_goals + 1)
        assert all(item.continuation_of for item in calls[additional_goals + 1:])
    asyncio.run(run())


def _setup(worker, *, store=None, checkpointer=None):
    registry = build_default_capability_registry("tenant-target")
    store = store or InMemoryConversationStateStore()
    orchestration = OrchestrationRuntime(direct_executor=worker,
        domain_workers={"product_technical": worker, "order_logistics": worker},
        checkpointer=checkpointer or InMemorySaver(serde=target_checkpoint_serializer()))
    manager = TargetConversationManager(state_store=store, registry=registry,
        understanding=_ResumeAwareUnderstanding(TurnProposal(ProposalDisposition.RESOLVED, (
            CommandProposal("product", CommandKind.DELEGATE_TASK, "product_technical", "Find the requested product"),
            CommandProposal("order", CommandKind.DELEGATE_TASK, "order_logistics", "Read order status")), "TEST")),
        orchestration=orchestration)
    return manager, orchestration, store


@pytest.mark.parametrize("reject_again", [False, True])
@pytest.mark.parametrize("backend", ["memory", "postgres"])
def test_reply_rejection_preserves_wait_and_does_not_repeat_work(reject_again, backend, request):
    from hashlib import sha256
    from application.response_assembly import AssembledResponse, ResponseAssemblyMode
    from application.turn_runtime import TurnRuntime

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
                    return AssembledResponse("Progress saved; the question is unavailable.", ResponseAssemblyMode.TEMPLATE, (), False,
                        "REJECT", "INCOMPLETE")
                return AssembledResponse("Found", ResponseAssemblyMode.TEMPLATE, (), False,
                    "PASS", "VALID", verified_text_sha256=sha256(b"Found").hexdigest())

        checkpoints = saver or InMemorySaver(serde=target_checkpoint_serializer())
        assembler = Assembler()
        runtime = TurnRuntime(manager=manager, response_assembler=assembler, checkpointer=checkpoints)
        observations = TurnObservations("Find the product and read my order")
        first = await runtime.execute(identity, observations)
        assert not first.assembled.verified
        reopened = TurnRuntime(manager=manager, response_assembler=assembler, checkpointer=checkpoints)
        result = await reopened.execute(identity, observations)
        assert result.managed.state_after.pending_interaction is not None
        assert result.assembled == first.assembled
        assert assembler.calls == 1
        assert calls.count("product_technical") == 1
        assert calls.count("order_logistics") == 1
        assert store.load(identity.tenant_id, identity.user_id, identity.conversation_id).pending_interaction is not None
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


def test_domain_question_is_published_and_resumes_after_postgres_reopen(fresh_postgres_database_url):
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
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "MISSING_REFERENCE", "test", working_messages=history,
                    missing_inputs=(MissingInputSpec("reply", item.work_item_id, "MISSING_REFERENCE",
                        "string", "Can you provide another reference?"),))
            if item.continuation_of:
                assert context.working_messages == history
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
                               "DONE", "test", candidate_response="Found")
        class Publication(_Publication):
            questions = []
            def publish_interaction(self, *args, **kwargs):
                self.questions.append(kwargs["challenge"])
                return super().publish_interaction(*args, **kwargs)
        publication, admission = Publication(), _Admission()
        pending = None
        for phase in ("first", "reopen"):
            pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=3))
            pool.open()
            try:
                async with AsyncPostgresCheckpointOwner(postgres_database_url, setup=True) as saver:
                    store = PostgresConversationStateStore(pool)
                    manager, _, _ = _setup(worker, store=store, checkpointer=saver)
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
    asyncio.run(run())
