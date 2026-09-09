"""User decisions remain current input, and resume the exact prepared operation."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from jsonschema import validate

from application.conversation_actions import action_proposal, planning_actions
from application.conversation_agent import ApprovalReplyUnaddressed, ConversationAgent, planning_output_schema
from application.deterministic_resolution import TurnObservations
from application.turn_planning import ProposalDisposition
from infrastructure.target_model_context import planning_context, planning_payload_from_request
from tests.test_approval_revision_lifecycle import pending_state
from tests.test_conversation_actions import calls, payload, provider


@pytest.mark.parametrize("decision", ["approve", "decline"])
@pytest.mark.parametrize("text", ["", "另外多久到？", "先告诉我运费再决定", "Use another address instead"])
def test_decision_is_current_user_input_not_runtime_background_and_capture_is_lossless(decision, text):
    data = payload()
    data["conversation_context"] = {"recent_messages": []}
    data.update(message=text, pending_approval={"approval_id": "a", "version": 2, "arguments": {"size": 42}},
                current_user_decision={"approval_id": "a", "version": 2, "decision": decision})
    before = copy.deepcopy(data)
    contract, messages = planning_context(data)
    blocks = [json.loads(block["text"]) for block in messages[-1].content]
    assert "current_user_decision" not in blocks[0]["runtime_context"]
    assert blocks[-1] == {"current_user_decision": data["current_user_decision"]}
    restored = planning_payload_from_request({"system": contract, "messages": [
        {"role": message.type, "content": message.content} for message in messages]})
    assert restored == before == data


def test_structured_answers_and_decision_are_both_current_user_input():
    data = payload()
    data.update(supplied_interaction_values=[{"target_work_item_id": "w", "field_name": "size", "value": 42}],
                current_user_decision={"approval_id": "a", "decision": "approve"})
    _, messages = planning_context(data)
    blocks = [json.loads(block["text"]) for block in messages[-1].content]
    for key in ("supplied_interaction_values", "current_user_decision"):
        assert key not in blocks[0]["runtime_context"]
        assert {key: data[key]} in blocks[2:]


@pytest.mark.parametrize("typed", [True, False])
def test_current_decision_omission_is_not_a_successful_conversation_response(typed):
    state, _, _ = pending_state(False)
    obs = TurnObservations("Also tell me delivery time", approval_id="approval", approval_decision=typed)
    agent = ConversationAgent(SimpleNamespace())
    with pytest.raises(ApprovalReplyUnaddressed):
        agent._validate_and_compile({"status": "respond", "response": "Please confirm again."},
                                   obs, state, None, None, ())
    assert state.pending_approval.approval_id == "approval"
    assert not state.accepted_approvals


@pytest.mark.parametrize("typed", [True, False, None])
@pytest.mark.parametrize("decision", ["approve", "decline", "hold"])
def test_native_decision_and_independent_question_share_one_model_call(typed, decision):
    data = payload()
    data.update(pending_approval={"approval_id": "approval"},
                current_user_decision=None if typed is None else {
                    "approval_id": "approval", "decision": "approve" if typed else "decline"})
    native, models = provider(
        ("review_action", {"decision": decision}),
        ("knowledge_search", {"query": "Delivery time for the selected item"}))
    result = asyncio.run(native.plan(data))
    assert result["approval_decision"] == {"approval_id": "approval", "decision": decision}
    assert result["goals"][0]["resolved_query"] == "Delivery time for the selected item"
    assert sum(model.calls for model in models.values()) == 1


@pytest.mark.parametrize("text", ["先不要执行。", "Wait until I decide."])
def test_hold_can_reply_without_recreating_an_approval_or_task(text):
    data = payload()
    data["pending_approval"] = {"approval_id": "approval"}
    raw = action_proposal(planning_actions(data), calls(
        ("review_action", {"decision": "hold"}), ("respond", {"response": text})), "")
    validate(raw, planning_output_schema())
    state, _, _ = pending_state(False)
    original = state.fingerprint
    proposal = ConversationAgent(SimpleNamespace())._validate_and_compile(raw,
        TurnObservations("Wait", approval_id="approval", approval_decision=True), state, None, None, ())
    assert proposal.disposition is ProposalDisposition.RESPOND
    assert proposal.response_text == text
    assert proposal.approval_decision is None and not proposal.commands
    assert state.fingerprint == original


def test_preparation_tool_contract_does_not_embed_execution_call_instructions():
    from tests.test_approval_conversation import domain
    agent, context, _, _ = domain([])
    action = context.work_item.allowed_actions[0]
    tool = agent._action_tool(action)
    assert "Original business operation description" not in tool.description
    assert "business_operation_reference" in tool.description
    assert '"order_cancel": "Cancel order"' in agent._system(context)


def test_hold_algebra_requires_a_reply_or_real_followup_work():
    from jsonschema import ValidationError
    raw = {"status": "resolved", "approval_decision": {"approval_id": "a", "decision": "hold"}}
    with pytest.raises(ValidationError):
        validate(raw, planning_output_schema())
    validate({**raw, "status": "respond", "response": "I will wait."}, planning_output_schema())
