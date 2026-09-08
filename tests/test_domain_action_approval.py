from langgraph.store.memory import InMemoryStore
"""Domain proposal -> exact approval -> governed write -> domain continuation."""
import asyncio
import json
from dataclasses import replace
import pytest

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from application.conversation_state import InMemoryConversationStateStore, WorkControlStatus
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations, DeterministicResolutionError
from application.orchestration_runtime import OrchestrationRuntime
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal, TurnPlanningError
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresMigrationRunner
from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.target_workflow_execution import TargetWorkflowExecutor
from mcp.tool_manager import MCPToolManager, Tool, ToolEffectReceipt, ToolEffectStatus
from tests.test_target_framework_agent import ScriptedToolModel
from tests.test_target_persistence_and_manager import _identity, _ResumeAwareUnderstanding


@pytest.mark.parametrize("independent_count", [0, 1, 3])
def test_decline_resumes_only_independent_objectives(independent_count):
    from application.deterministic_resolution import DeterministicResolution, ResolutionKind
    from application.target_understanding import StateBoundTargetUnderstanding
    from tests.test_target_framework_agent import _item
    from tests.test_conversation_agent import _state

    state = _state()
    origin = replace(_item(), work_item_id="origin")
    downstream = replace(origin, work_item_id="dependent", dependencies=("origin",))
    independent = tuple(replace(origin, work_item_id=f"other-{index}")
                        for index in range(independent_count))
    resolution = DeterministicResolution(
        ResolutionKind.APPROVAL_DECISION, "DECLINED", state.fingerprint,
        approved=False, resumed_work_items=(origin, downstream, *independent),
        action_origin_work_item_id="origin",
    )
    proposal = asyncio.run(StateBoundTargetUnderstanding()(
        TurnObservations("No"), state, resolution,
        build_default_capability_registry("tenant-a")))
    assert len(proposal.commands) == independent_count
    assert all(not command.dependencies for command in proposal.commands)
    assert proposal.disposition is (ProposalDisposition.RESOLVED if independent_count
                                    else ProposalDisposition.CLARIFY)


def test_resumed_objectives_preserve_dependency_order():
    from application.target_understanding import StateBoundTargetUnderstanding
    from tests.test_target_framework_agent import _item
    from tests.test_conversation_agent import _state

    first = replace(_item(), work_item_id="first")
    second = replace(first, work_item_id="second", dependencies=("first",))
    commands = StateBoundTargetUnderstanding._continuations(
        (first, second), _state(), after="approved-action")
    assert commands[0].dependencies == ("approved-action",)
    assert commands[1].dependencies == (commands[0].command_id, "approved-action")


@pytest.mark.parametrize("waiting_status", ["NEEDS_USER_INPUT", "WAITING_APPROVAL", "BLOCKED"])
@pytest.mark.parametrize("same_thread", [True, False])
def test_existing_explicit_approval_retains_only_same_checkpoint_work(waiting_status, same_thread):
    from types import SimpleNamespace
    from application.action_approval import bind_action_approval
    from application.agent_result import AgentResultStatus
    from application.conversation_state import ConversationState, PendingApprovalState, WorkstreamState, WorkstreamStatus
    from tests.test_target_framework_agent import _item

    state = ConversationState.empty(tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a")
    state = state.start_workstream(WorkstreamState(
        "action-stream", "order_logistics", "order.cancel:v1", "READY", WorkstreamStatus.ACTIVE, 1))
    state = state.wait_for_approval(PendingApprovalState(
        "approval", 1, "action-stream", "prepared-action", "order.cancel:v1", "operation",
        "order:R1", "1", "2099-01-01T00:00:00+00:00", checkpoint_thread_id="thread"))
    item = _item()
    board = SimpleNamespace(results=(SimpleNamespace(work_item_id=item.work_item_id,
        pending_action=None, status=AgentResultStatus(waiting_status)),))
    next_state = bind_action_approval(state, SimpleNamespace(work=SimpleNamespace(items=(item,))),
                                      board, None, "thread" if same_thread else "other-thread")
    if not same_thread:
        assert next_state is state
        return
    assert next_state.pending_approval.suspended_work_items == (item,)
    assert next_state.pending_approval.origin_work_item_id is None
    assert next_state.pending_approval.operation_key == "operation"
    assert next_state.version == state.version + 1
    assert bind_action_approval(next_state, SimpleNamespace(work=SimpleNamespace(items=(item,))),
                                board, None, "thread") is next_state


@pytest.mark.parametrize("decision", ["approve", "deny", "supersede", "ask_first", "ask_twice", "clarify_during_approval", "cancel_both", "invalid_followup"])
def test_domain_action_approval_roundtrip_and_continuation(postgres_database_url, decision):
    async def run():
        base = build_default_capability_registry("tenant-target")
        action = replace(base.action("order.cancel:v1"), flow_ref=None)
        owner = replace(base.agent("order_logistics"), allowed_tool_ids=(
            "order_lookup", "order_cancel", "order_cancel_status"), allowed_skill_ids=())
        registry = replace(base, agents=(owner,), actions=(action,), flows=(), skills=())
        calls = []
        tools = MCPToolManager("test-key", model="test-model")

        async def lookup(params, context):
            calls.append("read")
            return {"order_id": params["order_id"], "status": "paid", "version": 4}

        async def cancel(params, context):
            calls.append(("write", params))
            return ToolEffectReceipt({"cancelled": True}, ToolEffectStatus.COMMITTED, "cancel-receipt")

        schema = {"type": "object", "properties": {"order_id": {"type": "string"}},
                  "required": ["order_id"], "additionalProperties": False}
        tools.register(Tool("order_lookup", "Read order", lookup, schema,
                            allowed_agents=("general",), authority="order.current_state"))
        tools.register(Tool("order_cancel_status", "Read cancellation", lookup, schema,
                            allowed_agents=("general",), authority="order.cancellation_state"))
        tools.register(Tool("order_cancel", "Cancel order", cancel,
                            {**schema, "properties": {**schema["properties"], "expected_order_version": {"type": "integer"}}},
                            allowed_agents=("general",), authority="order.cancel_action",
                            read_only=False, requires_approval=True, receipt_schema_version="action-receipt-v1"))
        model = ScriptedToolModel(responses=[
            *([AIMessage(content="", tool_calls=[{"name": "request_user_input",
                "args": {"question": "Which account?" if decision == "ask_twice" else "Which order should I cancel?"},
                "id": "need-order"}])] if decision in {"ask_first", "ask_twice"} else []),
            *([AIMessage(content="", tool_calls=[{"name": "request_user_input",
                "args": {"question": "Which order should I cancel?"}, "id": "second-question"}])]
                if decision == "ask_twice" else []),
            AIMessage(content="", tool_calls=[{"name": "prepare_order_cancel",
                "args": {"order_id": "DP1234"}, "id": "cancel-proposal"}]),
            AIMessage(content="Order DP1234 has not been cancelled. Shall I cancel it?"),
            *([AIMessage(content="", tool_calls=[{"name": "request_user_input",
                "args": {"question": "Which part would you like explained?"}, "id": "clarify"}]),
               AIMessage(content="The cancellation is still awaiting your decision.")]
              if decision in {"clarify_during_approval", "cancel_both"} else []),
            *([AIMessage(content="", tool_calls=[{"name": "request_user_input",
                "args": {"question": "Should I cancel it?"}, "id": f"invalid-{index}"}])
               for index in range(2)] if decision == "invalid_followup" else
              [AIMessage(content="Your cancellation has been completed.")]),
        ])
        class ObservedDomain(TargetFrameworkAgent):
            contexts = []
            results = []

            async def __call__(self, context):
                self.contexts.append(context)
                result = await super().__call__(context)
                self.results.append(result)
                return result

        domain = ObservedDomain(model, tools, review_model=model, review_available_tokens=14200,
                                result_store=InMemoryStore(), registry=registry, system_prompt=owner.description)
        PostgresMigrationRunner(postgres_database_url).upgrade()
        pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
        pool.open()
        try:
            store = InMemoryConversationStateStore()
            manager = TargetConversationManager(
                state_store=store, registry=registry,
                understanding=_ResumeAwareUnderstanding(TurnProposal(
                    ProposalDisposition.RESOLVED, (CommandProposal("open-order", CommandKind.DELEGATE_TASK,
                        owner.agent_id, "Cancel order DP1234 then explain the result",
                        allow_action_proposals=True),), "OPEN")),
                orchestration=OrchestrationRuntime(
                    direct_executor=domain, domain_workers={owner.agent_id: domain},
                    workflow_executor=TargetWorkflowExecutor(pool, tools, registry=registry),
                    checkpointer=InMemorySaver(serde=target_checkpoint_serializer()),
                ),
            )
            first = await manager.handle(_identity(f"{decision}-propose"), TurnObservations("Cancel order DP1234"))
            replies = ("customer-x", "DP1234") if decision == "ask_twice" else ("DP1234",) if decision == "ask_first" else ()
            for index, reply in enumerate(replies):
                assert first.state_after.pending_interaction is not None
                assert calls == []
                interaction = first.state_after.pending_interaction
                first = await manager.handle(_identity(f"supply-{index}"), TurnObservations(
                    reply, interaction_id=interaction.interaction_id,
                    interaction_version=interaction.version))
                resolutions = [entry["data"]["artifact"]["resolution"]
                    for entry in domain.results[-1].working_messages
                    if entry.get("type") == "tool" and (entry["data"].get("artifact") or {}).get("schema") == "resolved-interaction-v1"]
                assert [value["reply"] for value in resolutions if value["status"] == "ANSWERED"] == list(replies[:index + 1])
                assert all(not value["approval_granted"] for value in resolutions)
            pending = first.state_after.pending_approval
            assert pending is not None, first.board
            assert calls == ["read"]
            assert model.calls == 2 + len(replies)
            assert first.board.results[0].candidate_response == "Order DP1234 has not been cancelled. Shall I cancel it?"
            restored = conversation_state_from_payload(conversation_state_to_payload(first.state_after))
            assert restored == first.state_after
            assert pending.suspended_work_items[0].allowed_actions == (action.ref,)
            assert dict((arg.name, arg.value) for arg in pending.arguments) == {
                "order_id": "DP1234", "expected_order_version": 4}
            if decision in {"clarify_during_approval", "cancel_both"}:
                question = await manager.handle(_identity("ask-about-approval"), TurnObservations(
                    "Before confirming, can you explain?"))
                clarification = question.state_after.pending_interaction
                assert clarification is not None
                assert question.state_after.pending_approval == pending
                assert clarification.checkpoint_thread_id != pending.checkpoint_thread_id
                assert domain.contexts[-1].pending_approval == pending
                assert "prepare_order_cancel" not in model.bound_tool_names
                assert "order_cancel" not in model.bound_tool_names
                prompt = json.loads(domain._build_prompt(domain.contexts[-1]))
                assert prompt["pending_approval"] == {
                    "action_ref": pending.action_ref,
                    "arguments": {arg.name: arg.value for arg in pending.arguments},
                    "status": "AWAITING_DECISION_NOT_EXECUTED",
                }
                if decision == "cancel_both":
                    from application.conversation_agent import ConversationAgent
                    from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
                    from tests.test_conversation_agent import Provider
                    provider = Provider({"status": "resolved", "goals": [
                        {"kind": "cancel_active_work", "revises_control_id": control.control_id}
                        for control in question.state_after.active_work_controls]})
                    manager._understanding = CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider))
                    prepared = await manager.prepare(_identity("cancel-both"), TurnObservations("Stop both tasks"))
                    assert set((prepared.resume_thread_id, *prepared.source_thread_ids)) == {
                        clarification.checkpoint_thread_id, pending.checkpoint_thread_id}
                    assert prepared.state.pending_interaction is prepared.state.pending_approval is None
                    result = await manager.execute(prepared)
                    close = manager._orchestration.cancel_interrupt
                    fail_once = True
                    async def interrupted_cleanup(**kwargs):
                        nonlocal fail_once
                        await close(**kwargs)
                        if kwargs["thread_id"] in prepared.source_thread_ids and fail_once:
                            fail_once = False
                            raise ConnectionError("cleanup acknowledgement lost")
                    manager._orchestration.cancel_interrupt = interrupted_cleanup
                    with pytest.raises(ConnectionError, match="acknowledgement"):
                        await manager.commit(result)
                    await manager.commit(result)  # Cleanup replay is harmless.
                    assert calls == ["read"]
                    for thread in (clarification.checkpoint_thread_id, pending.checkpoint_thread_id):
                        snapshot = await manager._orchestration.graph.aget_state({"configurable": {"thread_id": thread}})
                        assert not any(task.interrupts for task in snapshot.tasks)
                    return
                answered = await manager.handle(_identity("clarify-approval"), TurnObservations(
                    "Whether the order has been cancelled yet", interaction_id=clarification.interaction_id,
                    interaction_version=clarification.version))
                assert answered.state_after.pending_interaction is None
                assert answered.state_after.pending_approval == pending
                assert domain.contexts[-1].pending_approval == pending
                assert "prepare_order_cancel" not in model.bound_tool_names
                assert "order_cancel" not in model.bound_tool_names
                assert calls == ["read"]
            if decision == "supersede":
                updated = first.state_after.close_work_control(
                    pending.suspended_work_items[0].control, status=WorkControlStatus.CANCELLED)
                assert store.compare_and_set(first.state_after, updated)
                assert updated.pending_approval is None
                with pytest.raises(DeterministicResolutionError, match="stale or unknown"):
                    await manager.handle(_identity("approve-stale"), TurnObservations(
                        "Yes", approval_decision=True, approval_id=pending.approval_id))
                assert calls == ["read"]
                return
            if decision == "invalid_followup":
                from application.turn_runtime import TurnRuntime, InteractionAssemblyUnavailable
                from application.response_assembly import AssembledResponse, ResponseAssemblyMode
                from application.conversation_state import WorkstreamStatus
                class RejectInput:
                    fallback_locale = "en"
                    async def assemble(self, board, *, requested_inputs, **kwargs):
                        assert requested_inputs
                        current = store.load("tenant-target", "user-target", "conversation-target")
                        assert current.pending_interaction is None
                        assert current.pending_approval is None
                        assert next(s for s in current.workstreams if s.workstream_id == pending.workstream_id).status is WorkstreamStatus.COMPLETED
                        assert board.results[0].action_receipts[0].receipt_id == "cancel-receipt"
                        return AssembledResponse("", ResponseAssemblyMode.TEMPLATE, (), False,
                            "REJECT", "INVALID_INTERACTION", rejected_input_work_items=tuple(
                                s.target_work_item_id for s in requested_inputs),
                            interaction_feedback="The action already completed; no user choice remains.")
                checkpoints = InMemorySaver(serde=target_checkpoint_serializer())
                for _ in range(2):
                    runtime = TurnRuntime(manager, RejectInput(), checkpointer=checkpoints)
                    with pytest.raises(InteractionAssemblyUnavailable):
                        await runtime.execute(_identity("approve"), TurnObservations(
                            "Yes", approval_decision=True, approval_id=pending.approval_id))
                assert len([call for call in calls if isinstance(call, tuple)]) == 1
                assert model.calls == 4  # Two invalid questions, no endless repair on restart.
                return
            from application.turn_runtime import TurnRuntime
            from application.response_assembly import ResponseAssembler
            runtime = TurnRuntime(manager, ResponseAssembler(),
                checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
            second = (await runtime.execute(_identity("approve"), TurnObservations(
                "No" if decision == "deny" else "Yes", approval_decision=decision != "deny",
                approval_id=pending.approval_id))).managed
            assert second.state_after.pending_approval is None
            assert second.checkpoint_thread_id == first.checkpoint_thread_id
            if decision == "deny":
                assert calls == ["read"]
                assert model.calls == 2
                return
            assert len([call for call in calls if isinstance(call, tuple)]) == 1, [
                (result.status.value, result.reason_code) for result in second.board.results]
            assert second.board.results[0].action_receipts[0].receipt_id == "cancel-receipt"
            assert domain.contexts[-1].pending_approval is None
            resolutions = [entry["data"]["artifact"]["resolution"]
                for entry in domain.results[-1].working_messages
                if entry.get("type") == "tool" and (entry["data"].get("artifact") or {}).get("schema") == "resolved-interaction-v1"]
            committed = [value for value in resolutions if value["status"] == "COMMITTED"]
            assert committed and committed[0]["operation_key"] == pending.operation_key
            assert "prepare_order_cancel" in model.bound_tool_names
            assert "order_cancel" not in model.bound_tool_names
            assert model.calls == (5 if decision == "clarify_during_approval" else
                                   3 + len(replies))
        finally:
            pool.close()
    asyncio.run(run())
