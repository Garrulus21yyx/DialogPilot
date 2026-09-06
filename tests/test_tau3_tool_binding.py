"""Environment registration is metadata-driven, without benchmark task answers."""
import asyncio
from types import SimpleNamespace
import pytest
from application.capability_registry import CapabilityEffect
from evaluation.tau3_tool_binding import bind_environment
from mcp.tool_manager import MCPToolManager


@pytest.mark.parametrize("name", ["exchange_record", "update_preferences", "request_service"])
def test_environment_write_registration_and_observed_receipt(name):
    def definition(tool_name):
        return SimpleNamespace(name=tool_name, openai_schema={"function": {
            "name": tool_name, "description": "An environment capability.",
            "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}},
                           "required": ["record_id"]}}})
    environment = SimpleNamespace(
        get_tools=lambda: [definition("read_record"), definition(name)],
        get_policy=lambda: "Follow the environment policy.",
        tools=SimpleNamespace(tool_type=lambda tool: SimpleNamespace(value="read" if tool == "read_record" else "write")),
    )
    calls = []
    async def call(tool, arguments):
        calls.append((tool, arguments))
        return SimpleNamespace(content='{"updated": true}', error=False, id="call-1")
    manager = MCPToolManager("test-key", model="test-model")
    registry = bind_environment(environment, manager, call)
    action, = registry.actions
    assert action.flow_ref is None
    assert action.allowed_tool_ids == (name,)
    assert registry.tool(name).effect is CapabilityEffect.WRITE
    assert registry.planning_shortcuts == ()
    tool = next(tool for tool in manager.registered_tools if tool.name == name)
    receipt = asyncio.run(tool.handler({"record_id": "R1"}, {"business_operation_key": "op-1"}))
    assert receipt.receipt_id == "official-call:call-1"
    assert calls == [(name, {"record_id": "R1"})]
    status = next(tool for tool in manager.registered_tools if tool.name == "observed_operation_status")
    assert asyncio.run(status.handler({"operation_key": "missing"}, {}))["status"] == "UNKNOWN"
    assert asyncio.run(status.handler({"operation_key": "op-1"}, {}))["status"] == "COMMITTED"
