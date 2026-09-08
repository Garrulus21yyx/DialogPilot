"""Delegation carries the objective; direct shortcuts own their parameter binding."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, ValidationError

from application.conversation_actions import planning_actions
from application.conversation_agent import ConversationAgent, planning_output_schema, _GOALS
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver, BindingSource
from application.target_conversation_manager import TargetContextMessage, TargetTurnContext
from application.turn_planning import ProposalDisposition, RoutePolicy
from tests.test_conversation_agent import Provider, _state, _invoke
from tests.test_conversation_actions import payload


@pytest.mark.parametrize("kind", sorted(_GOALS - {"change_address"}))
def test_business_address_is_not_an_optional_field_on_other_commands(kind):
    # The internal interchange must close the same boundary as native tools.
    schema = planning_output_schema()["properties"]["goals"]["items"]
    goal = {"kind": kind}
    if kind == "delegate_task":
        goal.update(target_agent="order_logistics", objective="Requested change", allow_action_proposals=True)
    elif kind == "atomic_read":
        goal.update(target_agent="order_logistics", tool_id="order_lookup", arguments={})
    if kind in {"general_qa", "refund_policy", "invoice_qa", "product_qa"}:
        goal["resolved_query"] = "What are the applicable rules?"
    Draft202012Validator(schema).validate(goal)
    goal["new_address"] = "101 Highway"
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate(goal)


def test_native_delegation_does_not_extract_business_address():
    action = next(a for a in planning_actions(payload()) if a.name == "delegate_task")
    schema = action.schema
    assert "new_address" not in schema["properties"]
    with pytest.raises(ValidationError):
        Draft202012Validator(schema).validate({
            "target_agent": "order_logistics", "objective": "Change address",
            "allow_action_proposals": True, "new_address": "101 Highway",
        })


@pytest.mark.parametrize("current", ["Yes, the state is NY.", "Use the second address, not the first.", "是的，按刚才补充的信息办理。"])
def test_multiturn_delegation_preserves_context_without_parameter_claim(current):
    state = _state()
    registry = build_default_capability_registry("tenant-a")
    obs = TurnObservations(current)
    context = TargetTurnContext(recent_messages=(
        TargetContextMessage("user", "Please change my address to 101 Highway, New York, 10001.", "turn:1"),
        TargetContextMessage("assistant", "Which state should I use?", "turn:2"),
    ))
    context = replace(context, entity_bindings=EntityBindingResolver().resolve(obs, state, context))
    objective = "Update the requested account and eligible order addresses using the user's latest corrections."
    provider = Provider({"status": "resolved", "goals": [{
        "kind": "delegate_task", "target_agent": "order_logistics",
        "objective": objective, "allow_action_proposals": True,
    }]})
    result = asyncio.run(ConversationAgent(provider).plan(
        obs, state, DeterministicResolver().resolve(obs, state), registry, context))
    assert result.disposition is ProposalDisposition.RESOLVED
    command, = result.commands
    assert command.objective == objective
    assert not command.argument_bindings and not command.arguments
    RoutePolicy().accept(result, state, registry)
    assert "101 Highway" in str(provider.calls[0]["conversation_context"])


def test_direct_shortcut_retains_current_source_and_does_not_approve():
    proposal, state, registry = _invoke(ConversationAgent(Provider({
        "status": "resolved", "goals": [{"kind": "change_address", "order_id": "DP1234",
        "order_id_source_ref": "turn-message:current:reference:1", "new_address": "101 Highway"}],
    })), "Change DP1234 to 101 Highway")
    command, = proposal.commands
    binding = next(b for b in command.argument_bindings if b.field_name == "new_address")
    assert binding.source is BindingSource.CURRENT_MESSAGE
    assert binding.value == "101 Highway" and binding.belongs_to(state)
    assert proposal.approval_decision is None
    RoutePolicy().accept(proposal, state, registry)


def test_manager_delivers_original_address_and_correction_to_domain():
    from application.agent_result import AgentResult, AgentResultStatus
    from application.conversation_state import InMemoryConversationStateStore
    from application.orchestration_runtime import OrchestrationRuntime
    from application.target_conversation_manager import TargetConversationManager
    from tests.test_turn_runtime import _identity

    async def run():
        seen = []
        async def context_provider(*_args):
            return TargetTurnContext(recent_messages=(
                TargetContextMessage("user", "Use 101 Highway, New York, 10001.", "turn:1"),
                TargetContextMessage("assistant", "Please confirm the state.", "turn:2"),
            ))
        async def worker(context):
            seen.append(context)
            return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
                               AgentResultStatus.SUCCEEDED, "TEST_CONTEXT_RECEIVED", "test-v1")
        agent = ConversationAgent(Provider({"status": "resolved", "goals": [{
            "kind": "delegate_task", "target_agent": "order_logistics",
            "objective": "Prepare the requested address update using the user's corrections.",
            "allow_action_proposals": True,
        }]}))
        manager = TargetConversationManager(
            state_store=InMemoryConversationStateStore(),
            registry=build_default_capability_registry("tenant-a"),
            understanding=agent.plan, context_provider=SimpleNamespace(load=context_provider),
            orchestration=OrchestrationRuntime(direct_executor=worker,
                domain_workers={"order_logistics": worker}))
        prepared = await manager.prepare(_identity(), TurnObservations("Yes, the state is NY."))
        await manager.execute(prepared)
        context, = seen
        assert context.current_message == "Yes, the state is NY."
        assert any("101 Highway" in message for message in context.recent_relevant_turns)
        assert not context.work_item.arguments
    asyncio.run(run())
