from langgraph.store.memory import InMemoryStore
"""Normal replies and runtime-bound interaction tools on the native Agent loop."""
from dataclasses import replace
import pytest
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


def test_native_input_tool_schema_repair_uses_existing_model_loop():
    import asyncio
    from langchain_core.messages import AIMessage
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from tests.test_target_framework_agent import ScriptedToolModel, _manager
    calls = []
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "invalid", "name": "request_user_input", "args": {}}]),
        AIMessage(content="", tool_calls=[{"id": "valid", "name": "request_user_input",
            "args": {"question": "Which option?"}}]),
    ])
    agent = TargetFrameworkAgent(model, _manager(calls), result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist the user.")
    result = asyncio.run(agent(_context(replace(_item(), requirement_ids=(), max_steps=3))))
    assert result.status.value == "NEEDS_USER_INPUT"
    assert model.calls == 2
    assert calls == []


@pytest.mark.parametrize("reply", ["Hello!", "No matching item was found.", "Please contact support."])
def test_normal_text_is_a_candidate_not_a_model_owned_business_outcome(reply):
    result = _adapt_framework_result(_context(replace(_item(), requirement_ids=())), (), "test",
                                     allowed_authorities={}, candidate_response=reply)
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "DOMAIN_OUTCOME_NOT_ACCEPTED"
    assert result.candidate_response == reply
    assert result.action_receipts == ()


def test_text_does_not_replace_required_evidence():
    result = _adapt_framework_result(_context(), (), "test", allowed_authorities={},
                                     candidate_response="Everything is completed.")
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "FRAMEWORK_AGENT_REQUIREMENTS_MISSING"


def test_no_declared_outcome_is_not_success_even_with_no_requirements():
    result = _adapt_framework_result(_context(replace(_item(), requirement_ids=())), (), "test",
                                     allowed_authorities={})
    assert result.status.value == "TERMINAL_FAILURE"
    assert result.reason_code == "AGENT_RESPONSE_MISSING"


@pytest.mark.parametrize("other", ["request_user_input", "catalog_search"])
def test_interaction_batch_is_corrected_before_any_tool_executes(other):
    import asyncio
    from langchain_core.messages import AIMessage
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from tests.test_target_framework_agent import ScriptedToolModel, _manager
    calls = []
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "request_user_input", "id": "ask1", "args": {"question": "Which option?"}},
            {"name": other, "id": "other", "args": {"question": "Which size?"} if other == "request_user_input" else {"query": "x"}}]),
        AIMessage(content="", tool_calls=[{"name": "request_user_input", "id": "ask2", "args": {"question": "Which option and size?"}}]),
    ])
    result = asyncio.run(TargetFrameworkAgent(model, _manager(calls), result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(_context()))
    assert result.status.value == "NEEDS_USER_INPUT"
    assert model.calls == 2
    assert calls == []
    assert len(result.missing_inputs) == 1


@pytest.mark.parametrize("tool,args,status", [
    ("request_user_input", {"question": "Which account?"}, "NEEDS_USER_INPUT"),
    ("report_blocked", {"reason": "No supported account lookup is available."}, "BLOCKED"),
])
def test_interaction_tools_bind_runtime_identity_and_end_without_another_model_call(tool, args, status):
    import asyncio
    from langchain_core.messages import AIMessage
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from tests.test_target_framework_agent import ScriptedToolModel, _manager
    model = ScriptedToolModel(responses=[AIMessage(content="", tool_calls=[{
        "id": "interaction", "name": tool, "args": args}])])
    result = asyncio.run(TargetFrameworkAgent(model, _manager([]), result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(_context()))
    assert result.status.value == status
    assert model.calls == 1
    if status == "NEEDS_USER_INPUT":
        field, = result.missing_inputs
        assert field.target_work_item_id == _item().work_item_id
        assert field.question_hint == args["question"]
        assert result.candidate_response is None
