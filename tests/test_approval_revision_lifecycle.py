"""Approval lifecycle properties across goal revisions and turn presentation."""
import asyncio
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.conversation_state import (
    ConversationState, ConversationStateConflict, PendingApprovalState,
    ResumeBinding, WorkControlStatus, WorkstreamState, WorkstreamStatus,
)
from application.turn_runtime import TurnRuntime
from tests.test_work_control import _item
from tests.test_response_assembly import _board


def pending_state(explicit):
    origin = _item("origin", 1, work_item_id="origin-1")
    other = _item("other", 1, work_item_id="other-1")
    state = ConversationState.empty(tenant_id="tenant", user_id="user", conversation_id="conversation")
    state = state.accept_work_items((origin, other), invocation_key="initial")
    pending = PendingApprovalState("approval", 1, "stream", "action", "registered:v1",
        "operation", "entity", "v1", "2099-01-01T00:00:00+00:00",
        control=origin.control if explicit else None,
        suspended_work_items=() if explicit else (origin, other),
        origin_work_item_id=None if explicit else origin.work_item_id)
    state = state.wait_for_approval(pending, new_workstream=WorkstreamState(
        "stream", origin.owner_agent, "registered:v1", "PREPARED",
        WorkstreamStatus.WAITING_APPROVAL, 1))
    return replace(state, resume_bindings=(ResumeBinding("token", "stream", 1),)), origin, other


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("affected", ["origin", "other"])
@pytest.mark.parametrize("transition", ["revise", "cancel", "close"])
def test_only_origin_revision_retires_unexecuted_approval(explicit, affected, transition):
    state, origin, other = pending_state(explicit)
    from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
    state = conversation_state_from_payload(json.loads(json.dumps(conversation_state_to_payload(state))))
    target = origin if affected == "origin" else other
    if transition == "revise":
        revised = _item(target.control.control_id, 2, work_item_id="new-work")
        updated = state.accept_work_items((revised,), invocation_key="new")
    elif transition == "cancel":
        updated = state.accept_work_items((), invocation_key="new", cancelled_controls=(target.control,))
    else:
        updated = state.close_work_control(target.control, status=WorkControlStatus.SUPERSEDED)
    assert updated.version == state.version + 1
    assert updated.accepted_approvals == state.accepted_approvals
    assert updated.consumed_signal_ids == state.consumed_signal_ids  # No invented user denial.
    if affected == "origin":
        assert updated.pending_approval is None
        assert not updated.resume_bindings
        assert updated.workstreams[0].status is WorkstreamStatus.CANCELLED
        with pytest.raises(ConversationStateConflict):
            updated.consume_approval(approval_id="approval", approval_version=1, approved=True)
    else:
        assert updated.pending_approval == state.pending_approval
        assert updated.resume_bindings == state.resume_bindings
        assert updated.workstreams == state.workstreams
    assert updated.fingerprint != state.fingerprint


@pytest.mark.parametrize("explicit", [False, True])
def test_accepted_grant_remains_a_record_when_origin_changes(explicit):
    state, origin, _ = pending_state(explicit)
    accepted = state.consume_approval(approval_id="approval", approval_version=1, approved=True)
    revised = accepted.accept_work_items((_item("origin", 2, work_item_id="new"),), invocation_key="new")
    assert revised.accepted_approvals == accepted.accepted_approvals
    assert revised.accepted_approvals[0].control == origin.control
    from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
    restored = conversation_state_from_payload(json.loads(json.dumps(conversation_state_to_payload(revised))))
    assert restored.accepted_approvals == revised.accepted_approvals
    assert restored.fingerprint == revised.fingerprint


def test_conflicting_origin_is_rejected_and_unbound_legacy_revision_is_explicit():
    from application.conversation_state import ConversationStateError
    state, origin, other = pending_state(False)
    with pytest.raises(ConversationStateError, match="differs from its originating"):
        replace(state.pending_approval, control=other.control)
    legacy, _, _ = pending_state(True)
    legacy = replace(legacy, pending_approval=replace(legacy.pending_approval, control=None))
    with pytest.raises(ConversationStateConflict, match="lacks its originating control"):
        legacy.accept_work_items((_item("origin", 2, work_item_id="new"),), invocation_key="new")
    with pytest.raises(ConversationStateConflict, match="lacks its originating control"):
        legacy.close_work_control(origin.control, status=WorkControlStatus.CANCELLED)


def test_explicit_independent_continuation_keeps_its_downstream_dependency():
    from application.target_understanding import StateBoundTargetUnderstanding
    from application.turn_planning import CommandKind, CommandProposal, TurnProposal, ProposalDisposition
    state, origin, other = pending_state(False)
    child = replace(_item("child", 1, work_item_id="child-1"), dependencies=(other.work_item_id,))
    state = state.accept_work_items((child,), invocation_key="initial")
    state = replace(state, pending_approval=replace(state.pending_approval,
        suspended_work_items=(*state.pending_approval.suspended_work_items, child)))
    explicit = StateBoundTargetUnderstanding._continuations((other,), state)[0]
    proposal = TurnProposal(ProposalDisposition.RESOLVED, (
        CommandProposal("replace-origin", CommandKind.DELEGATE_TASK, origin.owner_agent,
            "new target", revises_control_id=origin.control.control_id),
        replace(explicit, command_id="explicit-continuation")), "REVISION")
    merged = StateBoundTargetUnderstanding.preserve_approval_continuations(proposal, state)
    assert len(merged.commands) == 3
    assert not any(command.kind is CommandKind.CANCEL_WORK for command in merged.commands)
    continued_child = next(command for command in merged.commands if command.continuation_of == child.work_item_id)
    assert continued_child.dependencies == ("explicit-continuation",)


@pytest.mark.parametrize("published", [False, True])
def test_turn_distinguishes_retained_approval_from_unpublished_presentation(published):
    state, _, _ = pending_state(True)
    captured = []
    class Assembler:
        fallback_locale = "en"
        async def assemble(self, board, **kwargs):
            captured.append(kwargs)
            from application.response_assembly import AssembledResponse, ResponseAssemblyMode
            return AssembledResponse("", ResponseAssemblyMode.CONVERSATION_COMPOSE,
                (), True, "PASS", "TEST", hashlib.sha256(b"").hexdigest())
    runtime = TurnRuntime(None, Assembler(), interaction_published=lambda *a, **k: published)
    managed = SimpleNamespace(board=_board(), state_after=state, state_before=state, diagnostics=(),
        plan=SimpleNamespace(route=SimpleNamespace(reason_code="SIDE_QUESTION")))
    asyncio.run(runtime._assemble_response({"managed": managed, "invocation": object(),
        "prepared": SimpleNamespace(context=None), "observations": SimpleNamespace(raw_text="How long does it take?")}))
    assert captured[0]["pending_approval"] == (None if published else state.pending_approval)
    if published:
        assert captured[0]["conversation_context"]["retained_approval"]["status"] == "AWAITING_DECISION_NOT_EXECUTED"


@pytest.mark.parametrize("kind", ["fields", "approval", "both"])
@pytest.mark.parametrize("published", [False, True])
def test_durable_interaction_can_be_presented_without_new_execution(kind, published):
    from application.agent_result import RequestedField
    from application.conversation_state import PendingInteractionState
    from application.response_assembly import ResponseAssembler
    from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
    from tests.test_knowledge_answer_boundary import Verifier
    state, _, other = pending_state(False)
    if kind == "fields":
        state = replace(state, pending_approval=None)
    if kind != "approval":
        state = state.wait_for_interaction(PendingInteractionState("question", 1,
            (RequestedField("reply", other.work_item_id, "string", question_hint="Which option?"),),
            (), (other,), "field-thread"))
    state = conversation_state_from_payload(json.loads(json.dumps(conversation_state_to_payload(state))))
    captured = []
    class Composer:
        async def compose(self, payload):
            captured.append(payload)
            return "Please confirm the operation and/or choose your option."
    runtime = TurnRuntime(None, ResponseAssembler(Composer(), knowledge_verifier=Verifier(True)),
        interaction_published=lambda *a, **k: published)
    managed = SimpleNamespace(board=None, state_after=state, state_before=state, interaction_questions=(), diagnostics=(),
        plan=SimpleNamespace(route=SimpleNamespace(reason_code="CLARIFY")))
    result = asyncio.run(runtime._assemble_response({"managed": managed, "invocation": object(),
        "prepared": SimpleNamespace(context=None), "observations": SimpleNamespace(raw_text="What next?")}))
    if published:
        assert result["assembled"] is None and captured == []
    else:
        assert result["assembled"].verified
        evidence = captured[0]["evidence"]
        assert bool(evidence["pending_actions"]) == (kind != "fields")
        assert bool(evidence["requested_inputs"]) == (kind != "approval")
        assert result["assembled"].approval_operation_key == ("operation" if kind != "fields" else "")
    assert managed.board is None and managed.state_after == state


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("independent_count", [0, 1, 3])
def test_revised_wait_preserves_checkpoint_progress_and_dependency_closure(cancel, independent_count):
    from langgraph.checkpoint.memory import InMemorySaver
    from application.agent_result import AgentResult, AgentResultStatus
    from application.conversation_state import InMemoryConversationStateStore
    from application.default_capability_registry import build_default_capability_registry
    from application.deterministic_resolution import TurnObservations
    from application.orchestration_runtime import OrchestrationRuntime
    from application.target_conversation_manager import TargetConversationManager
    from application.turn_planning import CommandKind, CommandProposal, TurnProposal, ProposalDisposition
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    from tests.test_target_persistence_and_manager import _identity

    async def run():
        registry = build_default_capability_registry("tenant-target")
        store = InMemoryConversationStateStore()
        calls = []
        async def worker(context):
            item = context.work_item
            calls.append(item.objective)
            return AgentResult(item.work_item_id, item.owner_agent,
                AgentResultStatus.WAITING_APPROVAL if item.objective == "origin" else AgentResultStatus.SUCCEEDED,
                "SCRIPTED_OUTCOME", "test-v1")
        def command(name, **kwargs):
            return CommandProposal(name, CommandKind.DELEGATE_TASK,
                "order_logistics", name, **kwargs)
        initial = TurnProposal(ProposalDisposition.RESOLVED, (
            command("origin", allow_action_proposals=True), command("completed"),
            *(command(f"independent-{i}", allow_action_proposals=True) for i in range(independent_count)),
            command("dependent", dependencies=("origin",))), "INITIAL")
        async def understanding(observations, state, *args):
            if state.pending_approval is None:
                return initial
            return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
                "change", CommandKind.CANCEL_WORK if cancel else CommandKind.DELEGATE_TASK,
                "order_logistics", "replacement", revises_control_id=state.pending_approval.origin_control.control_id,
            ),), "REVISED")
        runtime = OrchestrationRuntime(direct_executor=worker,
            domain_workers={"order_logistics": worker},
            checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
        manager = TargetConversationManager(state_store=store, registry=registry,
            understanding=understanding, orchestration=runtime)
        prepared = await manager.prepare(_identity("initial"), TurnObservations("Initial goals"))
        first = await manager.execute(prepared)
        items = first.plan.work.items
        origin = items[0]
        suspended = tuple(item for item in items if item.objective != "completed")
        pending = PendingApprovalState("approval", 1, "stream", "action", "order.cancel:v1",
            "operation", "order:DP1234", "v1", "2099-01-01T00:00:00+00:00",
            checkpoint_thread_id=first.checkpoint_thread_id, suspended_work_items=suspended,
            origin_work_item_id=origin.work_item_id, control=origin.control)
        waiting = first.state_after.wait_for_approval(pending, new_workstream=WorkstreamState(
            "stream", origin.owner_agent, "order.cancel:v1", "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1))
        assert store.compare_and_set(first.state_after, waiting)
        assert calls == ["origin", "completed"]
        revised = await manager.prepare(_identity("revise"), TurnObservations("Cancel" if cancel else "Use another target"))
        second = await manager.execute(revised)
        await manager.commit_progress(second)
        second = await manager.resolve_followup(revised, second)
        await manager.commit(second)
        previous_calls = tuple(calls)
        replayed = await manager.execute(revised)
        await manager.commit_progress(replayed)
        replayed = await manager.resolve_followup(revised, replayed)
        await manager.commit(replayed)
        assert tuple(calls) == previous_calls
        assert replayed.state_after.fingerprint == second.state_after.fingerprint
        assert second.checkpoint_thread_id == first.checkpoint_thread_id
        assert second.state_after.pending_approval is None
        assert calls.count("completed") == 1
        assert "dependent" not in calls
        assert all(calls.count(f"independent-{i}") == 1 for i in range(independent_count))
        assert calls.count("replacement") == (0 if cancel else 1)
        if second.board is not None:
            outcomes = {item.objective: result for item, result in second.board.outcome_items}
            assert outcomes["completed"].status is AgentResultStatus.SUCCEEDED
            assert outcomes["dependent"].status is AgentResultStatus.CANCELLED
            assert all(outcomes[f"independent-{i}"].status is AgentResultStatus.SUCCEEDED
                       for i in range(independent_count))
        else:
            snapshot = await runtime.graph.aget_state({"configurable": {"thread_id": first.checkpoint_thread_id}})
            outcomes = {result.work_item_id: result for result in snapshot.values["agent_results"]}
            assert not snapshot.next
            assert outcomes[origin.work_item_id].status is AgentResultStatus.CANCELLED
            assert outcomes[items[-1].work_item_id].status is AgentResultStatus.CANCELLED
    asyncio.run(run())
