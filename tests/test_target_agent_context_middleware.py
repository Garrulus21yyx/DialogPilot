import asyncio
import json
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from infrastructure.target_agent_middleware import AgentContextMiddleware


@pytest.mark.parametrize("records", [10, 100, 500])
def test_in_budget_structured_results_are_preserved(records):
    content = json.dumps([{"id": str(index), "attribute": "complete-evidence"}
                          for index in range(records)])
    messages = [AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "call"}]),
                ToolMessage(content=content, tool_call_id="call", artifact={"source": "record"})]
    request = SimpleNamespace(messages=messages, system_message="Use the supplied tools.", tools=[],
                              override=lambda **values: SimpleNamespace(**values))
    seen = []
    async def handler(value):
        seen.extend(value.messages)
    asyncio.run(AgentContextMiddleware(ContextBudgetManager(
        context_window_tokens=64000, reserved_output_tokens=1200, protocol_reserve_tokens=600,
    )).awrap_model_call(request, handler))
    assert seen == messages
    assert json.loads(seen[-1].content)[-1]["id"] == str(records - 1)


def test_overflow_is_typed_and_does_not_send_a_broken_json_prefix():
    request = SimpleNamespace(messages=[ToolMessage(content="x " * 20000, tool_call_id="call")],
                              system_message="", tools=[])
    async def handler(value):
        pytest.fail("overflow must be resolved before a provider call")
    with pytest.raises(ModelContextBudgetExceeded):
        asyncio.run(AgentContextMiddleware(ContextBudgetManager(
            context_window_tokens=2000, reserved_output_tokens=500, protocol_reserve_tokens=100,
        )).awrap_model_call(request, handler))
