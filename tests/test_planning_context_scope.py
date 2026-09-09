"""Complete request admission and scoped archival views, without model traffic."""
import asyncio
import copy

import pytest
from langchain_core.messages import AIMessage

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.historical_context_budget import fit_historical_payload
from core.model_policy import ModelProfile, ModelRole
from core.provider_context_budget import ProviderContextBudget, ProviderContextBudgetExceeded
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_model_context import planning_payload_from_request
from tests.test_historical_context_budget import historical


@pytest.mark.parametrize("body_size", [1000, 10000, 100000])
def test_parent_planning_does_not_reinflate_child_investigations(body_size):
    import json
    from dataclasses import replace
    from application.conversation_context import conversation_context_payload
    from application.target_conversation_manager import TargetTurnContext
    from application.work_item import ControlMode
    from tests.test_business_observation_continuity import observed_board
    board = observed_board()
    item = replace(board.work_items[0], control_mode=ControlMode.DELEGATED)
    fact = replace(board.results[0].facts[0], value_json=json.dumps({"catalog": "DETAIL" * body_size}, separators=(",", ":")))
    result = replace(board.results[0], facts=(fact,), candidate_response="Found the requested item; awaiting the user's choice.",
                     working_messages=({"type": "tool", "data": {"content": "PRIVATE_LOOP"}},))
    board = replace(board, work_items=(item,), results=(result,), facts=(fact,))
    view = conversation_context_payload(TargetTurnContext(observed_execution=board))["observed_execution"]
    assert "DETAIL" not in json.dumps(view) and "PRIVATE_LOOP" not in json.dumps(view)
    assert view["outcomes"][0]["domain_summary"] == result.candidate_response
    assert view["outcomes"][0]["status"] == result.status.value
    assert board.facts == (fact,) and board.results[0].working_messages == result.working_messages
    direct = replace(board, work_items=(replace(item, control_mode=ControlMode.DIRECT),))
    assert conversation_context_payload(TargetTurnContext(observed_execution=direct))["observed_execution"]["facts"][0]["value"] == json.loads(fact.value_json)


@pytest.mark.parametrize("unrelated_count", [1, 5, 15])
def test_worker_receives_dependencies_and_own_progress_not_sibling_investigations(unrelated_count):
    from dataclasses import replace
    from application.agent_result import AgentResult, AgentResultStatus
    from application.orchestration_runtime import OrchestrationRuntime, _scope_results
    from application.work_item import ControlMode, WorkPlan
    from tests.test_target_orchestration_runtime import _item, _fact
    prerequisite = _item("prerequisite", "product", ControlMode.DELEGATED, "product")
    worker = _item("target", "refund", ControlMode.DELEGATED, "refund", dependencies=("prerequisite",))
    siblings = [_item(f"other-{n}", "product", ControlMode.DELEGATED, f"other-{n}") for n in range(unrelated_count)]
    plan = WorkPlan((prerequisite, *siblings, worker), worker.work_item_id)
    results = [AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
               "DONE", "test", facts=(_fact(item, item.work_item_id),)) for item in (prerequisite, *siblings)]
    progress = _fact(worker, "already completed local step")
    runtime = OrchestrationRuntime(direct_executor=None, domain_workers={})
    state = {"work_plan": plan, "ready_items": (worker,), "agent_results": _scope_results(plan, results),
             "facts": tuple(fact for result in results for fact in result.facts),
             "continuation_facts": {worker.work_item_id: (progress,)},
             "continuation_messages": {worker.work_item_id: ({"local": "working history"},)},
             "retained_outcomes": tuple(zip(siblings, results[1:]))}
    packet = runtime._dispatch(state)[0].arg
    assert packet["facts"] == (results[0].facts[0], progress)
    assert packet["dependency_results"] == (results[0],)
    assert packet["working_messages"] == ({"local": "working history"},)
    assert state["facts"] == tuple(fact for result in results for fact in result.facts)


@pytest.mark.parametrize("turns", [10, 40, 100])
def test_public_dialogue_window_rolls_with_existing_summary_watermark(turns):
    from types import SimpleNamespace
    from memory.conversation_memory import MemoryContext, Message, MsgRole
    from infrastructure.target_turn_context import TargetTurnContextLoader
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from tests.test_target_turn_context import _identity, _state, Tools
    records = [Message(role=MsgRole.USER if n % 2 == 0 else MsgRole.ASSISTANT,
                       content=f"public-{n}", seq=n+1) for n in range(turns)]
    class Projection:
        async def get_projection_result(self, *args, **kwargs):
            return SimpleNamespace(state=SimpleNamespace(value="READY"), source_watermark=turns,
                reason_codes=(), context=MemoryContext(records, [], {}, "Only inspect; no submission authorized.",
                    summary_covered_until_seq=turns-8))
    observations, state = TurnObservations("Continue inspecting"), _state()
    context = asyncio.run(TargetTurnContextLoader(Projection(), Tools()).load(
        _identity(), observations, state, DeterministicResolver().resolve(observations, state)))
    assert [message.content for message in context.recent_messages] == [message.content for message in records[-8:]]
    assert context.summary.covered_until_seq == turns-8
    assert "no submission" in context.summary.content
    assert len(records) == turns


class CaptureModel:
    def __init__(self):
        self.calls = []
        self.tools = []

    def bind_tools(self, tools, **kwargs):
        self.tools = tools
        return self

    async def ainvoke(self, messages, config):
        self.calls.append((messages, config))
        return AIMessage(content="Which item do you mean?")


@pytest.mark.parametrize("body_size", [2000, 12000, 40000])
def test_pending_projection_externalizes_bodies_even_before_budget_threshold(body_size):
    original, entry = historical(body_size)
    payload = {"history": [entry], "message": "Approve this, but leave the other order unchanged.",
               "pending_approval": {"operations": [{"order": "A", "amount": 71.96}]},
               "active_work_controls": [{"objective": "Keep order B unchanged"}]}
    before = copy.deepcopy(payload)
    fitted = fit_historical_payload(ContextBudgetManager(context_window_tokens=100000), payload,
        observation_path=("history",), inline_publication_ids=frozenset())
    fact = fitted.payload["history"][0]["observation"]["facts"][0]
    assert "value_json" not in fact and fact["value_reference"]["tool"] == "read_conversation_observation"
    assert fitted.payload["pending_approval"] == payload["pending_approval"]
    assert fitted.payload["active_work_controls"] == payload["active_work_controls"]
    assert fitted.payload["message"] == payload["message"]
    assert payload == before and original.facts[0].value_json == entry["observation"]["facts"][0]["value_json"]
    assert fitted.report.final_tokens < fitted.report.original_tokens


@pytest.mark.parametrize("schema_chars", [4000, 12000, 18000])
def test_payload_that_fits_alone_is_reduced_for_actual_dynamic_schema(schema_chars):
    _, entry = historical(22000)
    value = {"message": "Approve A. Do not change B.", "supported_goals": [],
        "pending_approval": {"approval_id": "a1", "operations": [{"order": "A", "amount": 71.96}]},
        "active_work_controls": [{"control_id": "b1", "objective": "Do not change B"}],
        "conversation_context": {"business_observations": [entry], "recent_messages": [
            {"role": "user", "content": "Keep the original shoe size.", "seq": 1}]},
        "atomic_reads": [{"owner_agent": "retail", "tool_id": "read_conversation_observation",
            "description": "Read archived evidence. " + "x" * schema_chars,
            "input_schema": {"type": "object", "properties": {}}}]}
    # The old payload-only admission would pass; the final envelope needs work.
    projected = {k: v for k, v in value.items() if k != "atomic_reads"}
    assert ContextBudgetManager(context_window_tokens=8000).fit_payload(projected)
    model = CaptureModel()
    profile = ModelProfile("test", max_context_tokens=8000)
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
        model_profile=profile, synthesis_profile=profile, max_tokens=800)
    asyncio.run(provider.plan(value))
    messages, config = model.calls[0]
    request = {"system": messages[0].content, "tools": model.tools, "max_tokens": 800,
               "messages": [{"role": "assistant" if m.type == "ai" else "user", "content": m.content}
                            for m in messages[1:]]}
    usage = ProviderContextBudget().validate(profile, ModelRole.INTENT, request)
    assert usage.total_reserved_tokens <= 8000
    payload = planning_payload_from_request(request)
    assert "value_reference" in payload["conversation_context"]["business_observations"][0]["observation"]["facts"][0]
    assert payload["message"] == value["message"]
    assert payload["pending_approval"] == value["pending_approval"]
    assert payload["conversation_context"]["recent_messages"] == value["conversation_context"]["recent_messages"]
    assert config["metadata"]["context_budget"]["tool_schema_tokens"] == usage.tool_schema_tokens
    assert "value_json" in entry["observation"]["facts"][0]


def test_oversized_mandatory_request_never_reaches_model():
    model = CaptureModel()
    profile = ModelProfile("test", max_context_tokens=2048)
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
        model_profile=profile, synthesis_profile=profile, max_tokens=800)
    with pytest.raises(ProviderContextBudgetExceeded) as failure:
        asyncio.run(provider.plan({"message": "current user restriction " * 1000, "supported_goals": []}))
    assert failure.value.usage.total_reserved_tokens > profile.max_context_tokens
    assert failure.value.context_projection["final_tokens"] > failure.value.context_projection["available_tokens"]
    assert not model.calls


@pytest.mark.parametrize("covered", [0, 1])
def test_budget_trims_only_summarized_history_preserving_recent_exchange(covered):
    profile = ModelProfile("test", max_context_tokens=4096)
    model = CaptureModel()
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
        model_profile=profile, synthesis_profile=profile, max_tokens=800)
    value = {"message": "Yes, that size, inspect only.", "supported_goals": [],
        "conversation_context": {"summary": {"content": "The user authorizes inspection only.",
                                            "covered_until_seq": covered},
            "recent_messages": [{"role": "user", "seq": 1, "content": "old details " * 5000},
                                {"role": "user", "seq": 9, "content": "Keep the original size."},
                                {"role": "assistant", "seq": 10, "content": "Do you mean size M?"}]}}
    original = copy.deepcopy(value)
    if not covered:
        with pytest.raises(ProviderContextBudgetExceeded) as failure:
            asyncio.run(provider.plan(value))
        assert failure.value.context_projection["removed_items"] == ()
        assert not model.calls
    else:
        asyncio.run(provider.plan(value))
        messages, config = model.calls[0]
        assert [message.content for message in messages[2:4]] == ["Keep the original size.", "Do you mean size M?"]
        assert config["metadata"]["context_projection"]["removed_items"]
    assert value == original
