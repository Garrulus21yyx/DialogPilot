"""Direct execution consumes the same Registry principal and work envelope as agents."""
import asyncio
from dataclasses import replace

import pytest

from application.default_capability_registry import build_default_capability_registry
from application.work_item import ControlMode
from infrastructure.target_tool_execution import TargetToolExecutor
from mcp.tool_manager import MCPToolManager, Tool, ToolResult
from tests.test_target_framework_agent import _context, _item


@pytest.mark.parametrize("owner", ["retail", "external_domain", "general"])
@pytest.mark.parametrize("principal", [None, "environment_worker"])
@pytest.mark.parametrize("status", ["success", "timeout", "rejected"])
def test_direct_identity_and_envelope_are_registry_owned(owner, principal, status):
    registry = build_default_capability_registry("tenant-a")
    agent = replace(registry.agent("general"), agent_id=owner, tool_principal=principal)
    # Existing definitions remain intact; the arbitrary owner is an additional
    # valid capability, not an executor-specific case or alias.
    registry = replace(registry, agents=tuple(
        a for a in registry.agents if a.agent_id != owner
    ) + (agent,))
    tool_id = agent.allowed_tool_ids[0]
    item = replace(_item(allowed_tools=(tool_id,)), owner_agent=owner,
                   control_mode=ControlMode.DIRECT, requirement_ids=())
    context = _context(item)
    calls = []

    class Tools:
        async def execute_for_agent(self, name, arguments, **kwargs):
            calls.append((name, kwargs))
            return ToolResult(status == "success", {}, name, status=status)

    result = asyncio.run(TargetToolExecutor(Tools(), registry=registry)(context))
    name, call = calls[0]
    assert name == tool_id
    assert call["agent_type"] == (principal or owner)
    assert call["allowed_tool_ids"] == item.allowed_tools
    assert call["context"] == dict(context.trusted_context)
    assert result.owner_agent == owner
    assert result.retryable == (status == "timeout")


@pytest.mark.parametrize("authorized", [True, False])
def test_real_tool_manager_enforces_registered_principal(authorized):
    registry = build_default_capability_registry("tenant-a")
    agent = replace(registry.agent("general"), agent_id="external_domain",
                    tool_principal="external_reader")
    registry = replace(registry, agents=registry.agents + (agent,))
    tool_id = agent.allowed_tool_ids[0]
    tools = MCPToolManager("test-key", model="test-model")
    effects = []

    async def handler(arguments, context):
        effects.append(dict(context))
        return {"found": True}

    tools.register(Tool(tool_id, "Read fixture", handler,
                        {"type": "object", "properties": {}},
                        allowed_agents=("external_reader" if authorized else "other_reader",)))
    item = replace(_item(allowed_tools=(tool_id,)), owner_agent=agent.agent_id,
                   control_mode=ControlMode.DIRECT, requirement_ids=(), arguments=())
    result = asyncio.run(TargetToolExecutor(tools, registry=registry)(_context(item)))
    assert len(effects) == int(authorized)
    assert result.status.value == ("SUCCEEDED" if authorized else "TERMINAL_FAILURE")
