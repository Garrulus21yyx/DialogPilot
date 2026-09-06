import asyncio
from application.conversation_agent import ConversationAgent
from tests.test_conversation_agent import Provider, _invoke
from infrastructure.target_conversation_provider import (
    AnthropicConversationPlanningProvider,
)


def test_missing_field_schema_is_provided_by_validation_owner():
    provider = Provider({"status": "out_of_scope"})
    _invoke(ConversationAgent(provider), "想咨询售后政策")
    assert set(provider.calls[0]["missing_fields_schema"]) == {
        "order_id",
        "asset_id",
        "new_address",
        "customer_service_goal",
    }


import pytest
from types import SimpleNamespace
from core.model_policy import ModelProfile, ReasoningEffort


@pytest.mark.parametrize("method", ["plan", "compose"])
@pytest.mark.parametrize("effort", list(ReasoningEffort))
def test_conversation_transport_preserves_role_reasoning_and_budget(method, effort):
    profile = ModelProfile(
        "deepseek-v4-flash", effort, "deepseek",
        min_completion_tokens=1024 if effort is not ReasoningEffort.NONE else 0,
    )
    calls = []

    class Messages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{}')])

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=profile, max_tokens=800,
    )
    asyncio.run(getattr(provider, method)({"message": "policy question"}))
    request = calls[0]
    assert request["model"] == profile.model
    assert request["max_tokens"] == max(800, profile.min_completion_tokens)
    if effort is ReasoningEffort.NONE:
        assert request["extra_body"] == {"thinking": {"type": "disabled"}}
    else:
        assert request["extra_body"] == {
            "thinking": {"type": "enabled"}, "output_config": {"effort": effort.value},
        }


@pytest.mark.parametrize("method", ["plan", "compose"])
def test_full_conversation_request_rejects_overflow_before_transport(method):
    from core.provider_context_budget import ProviderContextBudgetExceeded
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 8192, 16000)

    class Messages:
        async def create(self, **kwargs):
            pytest.fail("over-budget input must never reach transport")

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=profile,
    )
    with pytest.raises(ProviderContextBudgetExceeded):
        asyncio.run(getattr(provider, method)({"message": "政策" * 12000}))


def test_agent_maps_final_provider_budget_failure_to_context_outcome():
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 1500, 1600)

    class Messages:
        async def create(self, **kwargs):
            pytest.fail("full prompt cannot fit and must not call API")

    provider = AnthropicConversationPlanningProvider(
        SimpleNamespace(messages=Messages()), model_profile=profile,
    )
    proposal, _, _ = _invoke(ConversationAgent(provider), "想咨询售后政策")
    assert proposal.reason_code == "CONTEXT_BUDGET_EXCEEDED"
