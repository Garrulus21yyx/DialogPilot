"""Domain proposal -> exact approval -> governed write -> domain continuation."""
import asyncio
from dataclasses import replace
import pytest

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from application.conversation_state import InMemoryConversationStateStore, WorkControlStatus
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
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


@pytest.mark.parametrize("decision", ["approve", "deny", "supersede", "ask_first"])
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
                "args": {"field_name": "order_id", "question": "Which order should I cancel?"},
                "id": "need-order"}])] if decision == "ask_first" else []),
            AIMessage(content="", tool_calls=[{"name": "order_cancel",
                "args": {"order_id": "DP1234"}, "id": "cancel-proposal"}]),
            AIMessage(content="Your cancellation has been completed."),
        ])
        domain = TargetFrameworkAgent(model, tools, registry=registry, system_prompt=owner.description)
        PostgresMigrationRunner(postgres_database_url).upgrade()
        pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=4))
        pool.open()
        try:
            store = InMemoryConversationStateStore()
            manager = TargetConversationManager(
                state_store=store, registry=registry,
                understanding=_ResumeAwareUnderstanding(TurnProposal(
                    ProposalDisposition.RESOLVED, (CommandProposal("open-order", CommandKind.DELEGATE_TASK,
                        owner.agent_id, "Cancel order DP1234 then explain the result"),), "OPEN")),
                orchestration=OrchestrationRuntime(
                    direct_executor=domain, domain_workers={owner.agent_id: domain},
                    workflow_executor=TargetWorkflowExecutor(pool, tools, registry=registry),
                    checkpointer=InMemorySaver(serde=target_checkpoint_serializer()),
                ),
            )
            first = await manager.handle(_identity("propose"), TurnObservations("Cancel order DP1234"))
            if decision == "ask_first":
                assert first.state_after.pending_interaction is not None
                assert calls == []
                interaction = first.state_after.pending_interaction
                first = await manager.handle(_identity("supply-order"), TurnObservations(
                    "DP1234", interaction_id=interaction.interaction_id,
                    interaction_version=interaction.version))
            pending = first.state_after.pending_approval
            assert pending is not None, first.board
            assert calls == ["read"]
            assert model.calls == (2 if decision == "ask_first" else 1)
            restored = conversation_state_from_payload(conversation_state_to_payload(first.state_after))
            assert restored == first.state_after
            assert pending.suspended_work_items[0].allowed_actions == (action.ref,)
            assert dict((arg.name, arg.value) for arg in pending.arguments) == {
                "order_id": "DP1234", "expected_order_version": 4}
            if decision == "supersede":
                updated = first.state_after.close_work_control(
                    pending.suspended_work_items[0].control, status=WorkControlStatus.CANCELLED)
                assert store.compare_and_set(first.state_after, updated)
                with pytest.raises(TurnPlanningError, match="superseded"):
                    await manager.handle(_identity("approve-stale"), TurnObservations(
                        "Yes", approval_decision=True, approval_id=pending.approval_id))
                assert calls == ["read"]
                return
            second = await manager.handle(_identity("approve"), TurnObservations(
                "No" if decision == "deny" else "Yes", approval_decision=decision != "deny", approval_id=pending.approval_id))
            assert second.state_after.pending_approval is None
            assert second.checkpoint_thread_id == first.checkpoint_thread_id
            if decision == "deny":
                assert calls == ["read"]
                assert model.calls == 1
                return
            assert len([call for call in calls if isinstance(call, tuple)]) == 1
            assert second.board.results[0].action_receipts[0].receipt_id == "cancel-receipt"
            assert model.calls == (3 if decision == "ask_first" else 2)
        finally:
            pool.close()
    asyncio.run(run())
