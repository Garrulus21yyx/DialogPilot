import asyncio
import pytest
from application.conversation_agent import ConversationAgent, ConversationProviderOutputError, planning_goal_descriptions
from core.model_policy import ModelProfile, ReasoningEffort
from core.provider_context_budget import ProviderContextBudgetExceeded
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from tests.framework_structured_stub import models, action_models
from tests.test_conversation_agent import Provider, _invoke


def provider(value=None, **kwargs):
    profile = ModelProfile("test")
    return AnthropicConversationPlanningProvider(models(value, **kwargs),
        model_profile=profile, synthesis_profile=profile)


def test_missing_fields_and_goal_meanings_have_one_owner():
    p = Provider({"status": "out_of_scope"})
    _invoke(ConversationAgent(p), "想咨询售后政策")
    payload = p.calls[0]
    assert set(payload["missing_fields_schema"]) == {"order_id", "asset_id", "new_address", "customer_service_goal"}
    assert set(payload["goal_descriptions"]) == set(payload["supported_goals"])
    assert all(payload["goal_descriptions"].values())
    copy = planning_goal_descriptions()
    copy["invented"] = "Invented"
    assert "invented" not in planning_goal_descriptions()


@pytest.mark.parametrize("calls,text,value", [
    ([], "I cannot perform that operation.", {"status": "respond", "response": "I cannot perform that operation."}),
    ([], "Which order?", {"status": "respond", "response": "Which order?"}),
    ([("knowledge_search", {"query": "退货政策"})], "", {"status": "resolved", "goals": [
        {"kind": "general_qa", "resolved_query": "退货政策"}]}),
])
def test_supported_states(calls, text, value):
    p = provider()
    p._models = action_models(*calls, text=text)
    assert asyncio.run(p.plan({"message": "test", "supported_goals": ["general_qa"]})) == value


@pytest.mark.parametrize("value", [
    None, {}, {"status": "unknown"}, {"status": "resolved", "goals": []},
    {"status": "out_of_scope", "goals": [{"kind": "order_status"}]},
    {"status": "insufficient_context", "missing_fields": []},
    {"status": "resolved", "goals": [{"kind": "order_status", "goal_id": 1}]},
    {"status": "resolved", "goals": [{"kind": "order_status", "order_id": {"value": "DP9303"}}]},
    {"status": "resolved", "goals": [{"kind": "order_status", "depends_on": ["a", "a"]}]},
])
def test_invalid_states(value):
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider(value).plan({"message": "test"}))


@pytest.mark.parametrize("method,name", [("plan", "submit_turn_plan"), ("compose", "submit_composed_response")])
@pytest.mark.parametrize("stop,count", [("max_tokens", 1), ("refusal", 1), ("tool_use", 0), ("tool_use", 2)])
def test_incomplete_or_multiple_outputs(method, name, stop, count):
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(getattr(provider({}, name=name, stop=stop, count=count), method)(
            {"message": "test", "allowed_claims": [{"claim_id": "id", "kind": "FACT"}]}))


@pytest.mark.parametrize("value", [None, {}, {"response": "ok", "used_claim_ids": "id"},
    {"response": "", "used_claim_ids": ["id"]}, {"response": "ok", "used_claim_ids": []},
    {"response": "ok", "used_claim_ids": ["id", "id"]},
    {"response": "ok", "used_claim_ids": [{}]}, {"response": "ok", "used_claim_ids": ["id"], "extra": 1}])
def test_composition_rejects_invalid_values(value):
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider(value, name="submit_composed_response").compose(
            {"allowed_claims": [{"claim_id": "id", "kind": "FACT"}]}))


@pytest.mark.parametrize("method", ["plan", "compose"])
def test_overflow_prevents_transport(method):
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 8192, 16000)
    stub = models({})
    p = AnthropicConversationPlanningProvider(stub, model_profile=profile, synthesis_profile=profile)
    from infrastructure.target_model_recovery import ContextRecoveryExhausted
    with pytest.raises(ProviderContextBudgetExceeded if method == "plan" else ContextRecoveryExhausted):
        asyncio.run(getattr(p, method)({"message": "政策" * 12000,
            "allowed_claims": [{"claim_id": "outcome:1", "kind": "WORK_ITEM_OUTCOME"}]}))
    assert all(model.calls == 0 for model in stub.values())


def test_budget_failure_is_not_provider_failure():
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 1500, 1600)
    p = AnthropicConversationPlanningProvider(models({}), model_profile=profile, synthesis_profile=profile)
    proposal, _, _ = _invoke(ConversationAgent(p), "想咨询售后政策")
    assert proposal.reason_code == "CONTEXT_BUDGET_EXCEEDED"


def test_composition_uses_its_own_context_budget():
    from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
    profile = ModelProfile("test")
    p = AnthropicConversationPlanningProvider(models(text="ok"), model_profile=profile, synthesis_profile=profile,
        synthesis_context_budget=ContextBudgetManager(context_window_tokens=2000,
            reserved_output_tokens=800, protocol_reserve_tokens=600))
    agent = ConversationAgent(p, context_budget=ContextBudgetManager(context_window_tokens=16000))
    with pytest.raises(ModelContextBudgetExceeded):
        asyncio.run(agent.compose({"allowed_claims": [{"text": "政策条件" * 2000}]}))
