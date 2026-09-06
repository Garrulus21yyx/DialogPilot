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
