"""Independent continuation-envelope adversarial witnesses."""
from dataclasses import replace
import pytest

from application.target_understanding import StateBoundTargetUnderstanding
from application.turn_planning import (
    CommandKind, CommandProposal, ProposalDisposition, RoutePolicy,
    TurnPlanCompiler, TurnProposal, TurnPlanningError,
)
from tests.test_target_turn_planning import _registry, _state, _invocation


def test_read_only_open_delegation_keeps_non_skill_tools_on_resume():
    registry = _registry()
    registry = replace(registry, agents=tuple(
        replace(agent, allowed_tool_ids=(*agent.allowed_tool_ids, "order_lookup"))
        if agent.agent_id == "product_technical" else agent
        for agent in registry.agents))
    state = _state()
    command = CommandProposal("read-goal", CommandKind.DELEGATE_TASK,
        "product_technical", "Read product and order information")
    accepted = RoutePolicy().accept(TurnProposal(
        ProposalDisposition.RESOLVED, (command,), "test"), state, registry)
    plan = TurnPlanCompiler().compile(accepted, state, registry, _invocation())
    original = plan.work.items[0]
    state = state.accept_work_items(plan.work.items, invocation_key=str(_invocation().invocation_key))
    assert "order_lookup" in original.allowed_tools
    assert original.allowed_skills and not original.allowed_actions
    resumed = StateBoundTargetUnderstanding._resume_command(1, original)
    after = RoutePolicy().accept(TurnProposal(
        ProposalDisposition.RESOLVED, (resumed,), "test"), state, registry)
    assert after.commands[0].allowed_tools == original.allowed_tools
    assert after.commands[0].allowed_actions == original.allowed_actions


@pytest.mark.parametrize("mismatch", ["owner", "registry", "revision"])
def test_resume_rejects_foreign_or_stale_internal_envelope(mismatch):
    registry, state = _registry(), _state()
    command = CommandProposal("goal", CommandKind.DELEGATE_TASK,
        "billing_refund", "Investigate refund eligibility")
    accepted = RoutePolicy().accept(TurnProposal(
        ProposalDisposition.RESOLVED, (command,), "test"), state, registry)
    plan = TurnPlanCompiler().compile(accepted, state, registry, _invocation())
    original = plan.work.items[0]
    state = state.accept_work_items(plan.work.items, invocation_key=str(_invocation().invocation_key))
    resumed = StateBoundTargetUnderstanding._resume_command(1, original)
    invalid = {
        "owner": replace(original, owner_agent="order_logistics"),
        "registry": replace(original, registry_fingerprint="foreign-registry"),
        "revision": replace(original, control=replace(original.control, revision=original.control.revision + 1)),
    }[mismatch]
    resumed = replace(resumed, resumed_work_item=invalid)
    with pytest.raises(TurnPlanningError, match="resumed capability envelope"):
        RoutePolicy().accept(TurnProposal(
            ProposalDisposition.RESOLVED, (resumed,), "test"), state, registry)
