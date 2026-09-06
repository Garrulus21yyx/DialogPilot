from dataclasses import replace
import pytest
from pydantic import ValidationError
from infrastructure.target_agent_result_adapter import DomainOutcome
from infrastructure.target_framework_agent import _adapt_framework_result
from tests.test_target_framework_agent import _context, _item


@pytest.mark.parametrize("field_count", [1, 2, 5])
@pytest.mark.parametrize("reply", ["I don't know; try another way", "A is blue, B is medium", "Please reconsider"])
def test_bound_text_reply_resumes_without_fabricating_slot_values(field_count, reply):
    from application.agent_result import RequestedField
    from application.conversation_state import ConversationState, PendingInteractionState, ConversationStateError
    from application.deterministic_resolution import DeterministicResolver, TurnObservations, ResolutionKind
    state = ConversationState.empty(tenant_id="tenant-a", user_id="user-a", conversation_id="conversation-a")
    item = _item()
    pending = PendingInteractionState("question", 1,
        tuple(RequestedField(f"field-{i}", item.work_item_id, "string") for i in range(field_count)),
        (), (item,), "thread")
    state = state.wait_for_interaction(pending)
    resolution = DeterministicResolver().resolve(TurnObservations(reply,
        interaction_id="question", interaction_version=1), state)
    assert resolution.kind is ResolutionKind.REPLY_PENDING_INPUT
    assert resolution.fields == ()
    assert resolution.resumed_work_items == (item,)
    updated = state.consume_interaction_reply(interaction_id="question", interaction_version=1)
    assert updated.pending_interaction is None
    assert updated.workstreams == state.workstreams
    with pytest.raises(ConversationStateError):
        updated.consume_interaction_reply(interaction_id="question", interaction_version=1)


def test_native_output_schema_repair_uses_existing_model_loop():
    import asyncio
    from langchain_core.messages import AIMessage
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from tests.test_target_framework_agent import ScriptedToolModel, _manager
    calls = []
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "invalid", "name": "DomainOutcome",
            "args": {"status": "NEEDS_USER_INPUT", "response": "Which option?", "missing_inputs": []}}]),
        AIMessage(content="", tool_calls=[{"id": "valid", "name": "DomainOutcome",
            "args": {"status": "NEEDS_USER_INPUT", "response": "Which option?",
                     "missing_inputs": [{"field_name": "option", "question": "Which option?"}]}}]),
    ])
    agent = TargetFrameworkAgent(model, _manager(calls),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Assist the user.")
    result = asyncio.run(agent(_context(replace(_item(), requirement_ids=(), max_steps=3))))
    assert result.status.value == "NEEDS_USER_INPUT"
    assert model.calls == 2
    assert calls == []


@pytest.mark.parametrize("status", ["SUCCEEDED", "BLOCKED", "NEEDS_USER_INPUT"])
def test_domain_disposition_is_explicit_and_independent_of_empty_coverage(status):
    outcome = DomainOutcome(status=status, response="A supported response.",
        missing_inputs=[{"field_name": "contact", "question": "How can we locate your account?"}]
        if status == "NEEDS_USER_INPUT" else [])
    result = _adapt_framework_result(_context(replace(_item(), requirement_ids=())), (), "test",
                                     allowed_authorities={}, domain_outcome=outcome)
    assert result.status.value == status
    assert bool(result.missing_inputs) == (status == "NEEDS_USER_INPUT")


def test_no_declared_outcome_is_not_success_even_with_no_requirements():
    result = _adapt_framework_result(_context(replace(_item(), requirement_ids=())), (), "test",
                                     allowed_authorities={})
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "DOMAIN_OUTCOME_MISSING"


@pytest.mark.parametrize("status,fields", [("NEEDS_USER_INPUT", []), ("SUCCEEDED", [{
    "field_name": "contact", "question": "Which account?"}])])
def test_invalid_outcome_input_algebra_is_rejected(status, fields):
    with pytest.raises(ValidationError):
        DomainOutcome(status=status, response="Response", missing_inputs=fields)
