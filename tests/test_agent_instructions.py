"""Role/state instruction combinations without live models or business writes."""
from dataclasses import replace
from itertools import product

import pytest

from application.agent_instructions import conversation_instructions
from application.action_approval import ACTION_INTERACTION_CONTRACT
from tests.test_approval_conversation import domain


@pytest.mark.parametrize("approval,inputs,active,resume", product((False, True), repeat=4))
def test_conversation_role_precedes_only_relevant_state_instructions(approval, inputs, active, resume):
    prompt = conversation_instructions({
        "pending_approval": {"approval_id": "a"} if approval else None,
        "pending_input": {"interaction_id": "i"} if inputs else None,
        "active_work_controls": [{"control_id": "c"}] if active else [],
        "resumable_work": [{"control_id": "c"}] if resume else [],
    })
    assert prompt.startswith("You are DialogPilot, the customer's ecommerce service assistant.")
    assert "Answer ordinary conversation directly" in prompt
    assert "direct read tool" in prompt
    assert "call the appropriate specialist subagent" in prompt
    assert "do not need to collect all fields" in prompt
    assert "does not schedule later work" in prompt
    assert "do not relabel their status in the objective" in prompt
    assert ("Pending action decision:" in prompt) == approval
    assert ("Pending information:" in prompt) == inputs
    assert ("Existing work:" in prompt) == (active or resume)
    assert ACTION_INTERACTION_CONTRACT not in prompt


@pytest.mark.parametrize("prepare,pending", product((False, True), repeat=2))
def test_specialist_instructions_match_actual_preparation_tool_exposure(prepare, pending):
    worker, context, _, _ = domain([])
    context = replace(context, work_item=replace(context.work_item,
        allowed_actions=context.work_item.allowed_actions if prepare else ()),
        pending_approval=object() if pending else None)
    prompt = worker._system(context)
    assert prompt.startswith("You are DialogPilot's order_logistics specialist subagent.")
    assert "Domain policy and expertise:\nAssist." in prompt
    assert "request_user_input" in prompt and "report_blocked" in prompt
    assert "planning description, not a business observation" in prompt
    assert "alone does not require another lookup" in prompt
    assert (ACTION_INTERACTION_CONTRACT in prompt) == (prepare and not pending)
    assert ("business_operation_reference" in prompt) == (prepare and not pending)
    assert ("Existing pending approval:" in prompt) == pending
    if prepare and not pending:
        assert "Cancel order" in prompt


def test_observed_execution_does_not_instruct_unavailable_state_actions():
    prompt = conversation_instructions({"pending_approval": {"approval_id": "a"},
        "pending_input": {"interaction_id": "i"},
        "conversation_context": {"observed_execution": {"run_id": "r"}}})
    assert "Observed work:" in prompt
    assert "Pending action decision:" not in prompt
    assert "Pending information:" not in prompt


@pytest.mark.parametrize("approval,waiting", product((False, True), repeat=2))
def test_reply_contract_distinguishes_waits_from_autonomous_execution(approval, waiting):
    from application.action_approval import reply_presentation_instruction
    context = {"pending_actions": [{"action": "example"}] if approval else [],
        "turn_execution": {"phase": "REPLY", "continues_after_reply": False,
                           "waiting_for_input": waiting, "waiting_for_approval": approval}}
    prompt = reply_presentation_instruction(context)
    assert "no further work is scheduled" in prompt
    assert "continuation requires that input" in prompt
    assert ("NO prepared action" in prompt) == (not approval)
