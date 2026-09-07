import copy

import pytest

from application.context_budget import (
    ContextBudgetManager,
    ModelContextBudgetExceeded,
)


def test_payload_budget_drops_only_declared_oldest_context_and_is_deterministic():
    manager = ContextBudgetManager(
        context_window_tokens=650,
        reserved_output_tokens=100,
        protocol_reserve_tokens=100,
    )
    payload = {
        "message": "现在查一下它",
        "active_workstreams": [{"id": "refund-1", "status": "ACTIVE"}],
        "conversation_context": {
            "summary": {"content": "用户只授权查询，没有授权退款申请。"},
            "recent_messages": [
                {"role": "user", "content": "旧消息" * 400},
                {"role": "assistant", "content": "最近回复"},
            ],
        },
    }
    original = copy.deepcopy(payload)
    first = manager.fit_payload(
        payload,
        trim_oldest_paths=("conversation_context.recent_messages",),
    )
    second = manager.fit_payload(
        payload,
        trim_oldest_paths=("conversation_context.recent_messages",),
    )
    assert payload == original
    assert first == second
    assert first.payload["message"] == payload["message"]
    assert first.payload["active_workstreams"] == payload["active_workstreams"]
    assert first.payload["conversation_context"]["summary"] == (
        payload["conversation_context"]["summary"]
    )
    assert first.report.final_tokens <= first.report.available_tokens
    assert first.report.removed_items == (
        "conversation_context.recent_messages[oldest]",
    )


def test_oversized_mandatory_payload_returns_typed_failure():
    manager = ContextBudgetManager(
        context_window_tokens=300,
        reserved_output_tokens=100,
        protocol_reserve_tokens=100,
    )
    with pytest.raises(ModelContextBudgetExceeded) as raised:
        manager.fit_payload({"message": "必须保留" * 500})
    assert raised.value.required_tokens > raised.value.available_tokens


def test_composition_context_is_not_silently_trimmed_or_mutated():
    import asyncio
    from application.conversation_agent import ConversationAgent
    class Provider:
        called = False
        async def compose(self, payload):
            self.called = True
            return payload
    provider = Provider()
    budget = ContextBudgetManager(context_window_tokens=650, reserved_output_tokens=100, protocol_reserve_tokens=100)
    agent = ConversationAgent(provider, synthesis_context_budget=budget)
    payload = {'current_message': '不是。', 'conversation_context': {
        'recent_messages': [{'role': 'assistant', 'content': '是质量问题吗？' * 1000, 'source_ref': 'turn:2'}]}}
    original = copy.deepcopy(payload)
    with pytest.raises(ModelContextBudgetExceeded):
        asyncio.run(agent.compose(payload))
    assert not provider.called and payload == original
