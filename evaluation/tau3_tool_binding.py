"""Bind an official environment to existing governed tools, without task access.

The benchmark provides no remote idempotency/status API. Observed responses are
receipts; an unobserved outcome stays unknown. This is not a production CAS or
remote exactly-once guarantee. All business calls go through the orchestrator.
"""
from __future__ import annotations

import json
from application.authority_policy import FactRequirement, RequirementEffect, AuthoritySupport
from application.capability_registry import (
    ActionDefinition, ActionReconciliationDefinition, AgentDefinition, ApprovalPolicy,
    CapabilityEffect, CapabilityRegistryBundle, CapabilityRisk, ToolDefinition,
    VerificationProfile,
)
from mcp.tool_manager import Tool, ToolEffectReceipt, ToolEffectStatus, ToolRisk, ToolRejected


def bind_environment(environment, manager, call):
    definitions, requirements, actions = [], [], []
    receipts = {}
    profile = "environment-evidence:v1"
    receipt_schema = "environment-observation-receipt-v1"
    official_tools = environment.get_tools()
    status_tool = "observed_operation_status"

    def register(name, description, schema, handler, *, write=False):
        authority = "environment." + name
        manager.register(Tool(
            name, description, handler, schema, allowed_agents=("retail",),
            authority=authority, read_only=not write, requires_approval=write,
            risk=ToolRisk.HIGH if write else ToolRisk.LOW,
            receipt_schema_version=receipt_schema if write else "",
            timeout_s=60,
        ))
        definitions.append(ToolDefinition(
            name, "v1", "environment-input-v1", "environment-output-v1",
            CapabilityEffect.WRITE if write else CapabilityEffect.READ,
            CapabilityRisk.HIGH if write else CapabilityRisk.LOW,
            authority, profile, receipt_schema if write else "",
        ))
        requirements.append(FactRequirement(
            authority, authority, (), None,
            RequirementEffect.WRITE if write else RequirementEffect.READ,
            (name,), (), receipt_schema if write else "", AuthoritySupport.SUPPORTED,
            "official-environment-adapter-v1",
        ))

    async def observed_status(params, context):
        key = params["operation_key"]
        return receipts.get(key, {"operation_key": key, "status": "UNKNOWN"})

    register(status_tool, "Read a previously observed adapter operation receipt; missing is UNKNOWN.",
             {"type": "object", "properties": {"operation_key": {"type": "string"}},
              "required": ["operation_key"]}, observed_status)

    for official in official_tools:
        name = official.name
        write = environment.tools.tool_type(name).value == "write"
        schema = official.openai_schema["function"]["parameters"]

        def handler_for(tool_name, is_write):
            async def handler(params, context):
                message = await call(tool_name, params)
                try:
                    value = json.loads(message.content or "null")
                except json.JSONDecodeError:
                    value = message.content
                data = value if isinstance(value, dict) else {"value": value}
                if message.error:
                    raise ToolRejected(str(message.content or "Environment rejected the request"), data=data)
                if not is_write:
                    return data
                receipt_id = "official-call:" + message.id
                operation_key = context["business_operation_key"]
                receipts[operation_key] = {"operation_key": operation_key,
                                          "receipt_id": receipt_id, "status": "COMMITTED"}
                return ToolEffectReceipt(data, ToolEffectStatus.COMMITTED, receipt_id)
            return handler

        register(name, official.openai_schema["function"]["description"], schema,
                 handler_for(name, write), write=write)
        if write:
            actions.append(ActionDefinition(
                name, "v1", "retail", None, CapabilityEffect.WRITE, CapabilityRisk.HIGH,
                ("environment." + name,), (name,), ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
                receipt_schema, ActionReconciliationDefinition(
                    status_tool, "environment." + status_tool, "operation_key", "operation_key",
                    (), "receipt_id"), profile,
            ))

    agent = AgentDefinition(
        "retail", "v1", tuple(tool.tool_id for tool in definitions), (),
        "environment-worker-v1", "environment-context-v1", profile,
        description=environment.get_policy(), timeout_seconds=120, max_model_calls=20,
    )
    return CapabilityRegistryBundle(
        "default", "tau3-retail-v1", (agent,), (), (), tuple(actions),
        tuple(requirements), tuple(definitions),
        (VerificationProfile("environment-evidence", "v1", ("authority", "receipt")),),
    )
