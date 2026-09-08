"""Project existing governed read tools into conversation planning inputs."""
from copy import deepcopy
from collections.abc import Mapping
from jsonschema import Draft202012Validator

from application.capability_registry import CapabilityEffect
from application.authority_policy import RequirementEffect, AuthoritySupport


class ConversationToolCatalog:
    def __init__(self, tool_manager):
        self._tools = tool_manager

    def __call__(self, registry, state, turn_context=None):
        context = {"tenant_id": str(state.tenant_id), "user_id": str(state.user_id),
                   "conversation_id": str(state.conversation_id),
                   "knowledge_filter_contract": turn_context.knowledge_filter_contract if turn_context else {}}
        reads = []
        for agent in registry.agents:
            read_ids = tuple(name for name in agent.allowed_tool_ids
                             if registry.tool(name).effect is CapabilityEffect.READ)
            for tool in self._tools.tools_for_agent(agent.execution_principal, allowed_tool_ids=read_ids):
                definition = registry.tool(tool.name)
                if not tool.read_only or tool.authority != definition.authority:
                    raise ValueError("conversation_tool_registry_mismatch")
                requirement = next((r for r in registry.requirements
                                    if r.requirement_id == definition.authority and tool.name in r.allowed_tools), None)
                if (requirement is None or requirement.authority != definition.authority
                        or requirement.effect is not RequirementEffect.READ
                        or requirement.support is not AuthoritySupport.SUPPORTED):
                    raise ValueError("conversation_tool_requirement_missing")
                schema = tool.input_schema(context)
                if not isinstance(schema, Mapping):
                    raise ValueError("conversation_tool_schema_requires_object_form")
                Draft202012Validator.check_schema(schema)
                if {"approved", "approval_token"}.intersection(schema.get("properties", {})):
                    raise ValueError("conversation_tool_schema_uses_reserved_control_fields")
                reads.append({"owner_agent": agent.agent_id, "tool_id": tool.name,
                              "description": tool.description,
                              "input_schema": deepcopy(schema),
                              "requirement_ids": [requirement.requirement_id]})
        return tuple(reads)
