"""Convert a domain tool proposal into a receipt-governed, approvable action.

This boundary owns argument preparation, not business sequencing. It never
executes a write; the existing action runtime executes accepted approvals.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from application.agent_result import AgentResult, AgentResultStatus
from application.capability_registry import ApprovalPolicy, CapabilityEffect
from application.work_item import ArgumentValue, ControlMode
from infrastructure.target_agent_result_adapter import fact_from_tool_result


class TargetActionPreparation:
    def __init__(self, registry, tool_manager, control_guard=None):
        self.registry = registry
        self.tools = tool_manager
        self.guard = control_guard

    async def prepare(self, context, action_ref, arguments, call_id, *, operation_plan=None):
        item = context.work_item
        action = self.registry.action(action_ref)
        if action.ref not in item.allowed_actions or action.owner_agent != item.owner_agent:
            raise ValueError("action proposal exceeds work envelope")
        if operation_plan is not None:
            from application.operation_plan import validate_operation_plan
            from application.action_compatibility import validate_action_compatibility
            rules = {"prepare_" + tool: registered.state_transition
                     for registered in self.registry.actions for tool in registered.allowed_tool_ids}
            name = "prepare_" + action.allowed_tool_ids[0]
            validate_operation_plan(operation_plan, selected_tool=name, allowed_tools=rules)
            validate_action_compatibility({"tool": name,
                "arguments": {**arguments, "operation_plan": operation_plan}}, rules)
        if self.guard is not None:
            self.guard.ensure_current(item, context.trusted_context)
        if action.approval_policy not in {
            ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED,
            ApprovalPolicy.USER_COMMAND_SUFFICIENT,
        }:
            return self._result(item, AgentResultStatus.BLOCKED, "ACTION_REQUIRES_EXTERNAL_APPROVAL")
        values = dict(arguments)
        preparation = action.preparation
        # A new/revised goal is a new instruction; unchanged continuation cannot
        # turn rejection into another approval request for the same parameters.
        if item.continuation_of and item.control:
            def user_values(arguments):
                return {key: value for key, value in arguments.items()
                        if preparation is None or key != preparation.target_version_argument}
            if any(decision["decision"] == "DECLINED" and decision["action_ref"] == action.ref
                   and decision["control_id"] == item.control.control_id
                   and user_values(decision["arguments"]) == user_values(values)
                   for decision in context.trusted_context.get("action_decisions", ())):
                return self._result(item, AgentResultStatus.BLOCKED, "ACTION_PREVIOUSLY_DECLINED")
        facts = ()
        if preparation is not None:
            definition = next(tool for tool in self.tools.registered_tools
                              if tool.name == preparation.tool_id)
            params = {key: value for key, value in values.items()
                      if key in definition.schema.get("properties", {})}
            outcome = await self.tools.execute_for_agent(
                preparation.tool_id, params,
                agent_type=self.registry.agent(item.owner_agent).execution_principal,
                context=dict(context.trusted_context), call_id=f"{call_id}:prepare",
                allowed_tool_ids=(preparation.tool_id,),
            )
            if not outcome.success:
                return self._result(item, AgentResultStatus.BLOCKED, "ACTION_PREPARATION_UNAVAILABLE")
            data = outcome.data
            if outcome.authority != preparation.requirement_id or not isinstance(data, dict):
                return self._result(item, AgentResultStatus.BLOCKED, "ACTION_PREPARATION_INVALID")
            facts = (fact_from_tool_result(item, outcome),)
            if data.get(preparation.readiness_field) != preparation.readiness_value:
                return self._result(item, AgentResultStatus.BLOCKED, "ACTION_NOT_ELIGIBLE", facts=facts)
            version = data.get(preparation.target_version_field)
            if version is None or version == "":
                return self._result(item, AgentResultStatus.BLOCKED, "ACTION_TARGET_VERSION_MISSING")
            values[preparation.target_version_argument] = version
        else:
            version = str(item.state_snapshot_version)
        if self.guard is not None:
            self.guard.ensure_current(item, context.trusted_context)
        scope = context.trusted_context
        subject = {name: values[name] for name in action.reconciliation.passthrough_arguments}
        subject = subject or {"user_id": scope["user_id"], "conversation_id": scope["conversation_id"]}
        target = action.owner_agent + ":" + json.dumps(subject, sort_keys=True, ensure_ascii=False)
        identity = json.dumps({
            "invocation": scope["invocation_key"], "work": item.work_item_id,
            "control": item.control.__dict__ if item.control else None,
            "call": call_id, "action": action.ref, "arguments": values,
        }, sort_keys=True, ensure_ascii=False)
        operation_key = "action:v1:" + hashlib.sha256(identity.encode()).hexdigest()
        pending = replace(
            item, work_item_id=f"{item.work_item_id}:action:{call_id}",
            control_mode=ControlMode.WORKFLOW if action.flow_ref else ControlMode.ACTION,
            allowed_tools=(*action.allowed_tool_ids, action.reconciliation.tool_id),
            allowed_skills=(), allowed_actions=(), skill_hint=None, continuation_of=None,
            arguments=tuple(ArgumentValue.create(key, value) for key, value in sorted(values.items())),
            argument_bindings=(), dependencies=(), requirement_ids=action.requirement_ids,
            effect=CapabilityEffect.WRITE, risk=action.risk,
            expected_output_schema=action.receipt_schema_version,
            verification_profile=action.verification_profile,
            flow_ref=action.flow_ref, action_ref=action.ref,
            operation_key=operation_key, approval_binding="approval:" + operation_key,
            target_entity_version=str(version), aggregate_ref=target,
            reconciliation=action.reconciliation, approval_policy=action.approval_policy,
        )
        return self._result(item, AgentResultStatus.WAITING_APPROVAL, "ACTION_PROPOSED",
                            pending_action=pending, facts=facts)

    @staticmethod
    def _result(item, status, reason, **values):
        return AgentResult(item.work_item_id, item.owner_agent, status, reason,
                           "action-preparation-v1", **values)
