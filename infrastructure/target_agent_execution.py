"""Thin adapter from Target WorkItems to the existing bounded ReAct agents."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping

from agents.agent_orchestrator import BaseAgent, Request
from agents.react_engine import ReActCapability
from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk, TaskSpec
from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.capability_registry import (
    CapabilityEffect,
    CapabilityRegistryBundle,
    CapabilityRisk,
)
from application.orchestration_runtime import AgentContextView, WorkExecutor
from application.work_item import ArgumentValue, ControlMode
from mcp.tool_manager import ToolCallStatus, ToolEffectStatus, ToolResult


_TARGET_AGENT_TYPE = {
    "general": AgentType.GENERAL,
    "product_technical": AgentType.TECHNICAL,
    "order_logistics": AgentType.GENERAL,
    "billing_refund": AgentType.BILLING,
    "account_security": AgentType.ACCOUNT_SECURITY,
    "human_service": AgentType.ESCALATION,
}


class TargetAgentExecutor:
    """Run exactly one selected domain Agent inside a compiled capability envelope."""

    version = "target-react-agent-adapter-v1"

    def __init__(
        self,
        agents: Mapping[AgentType, BaseAgent],
        *,
        registry: CapabilityRegistryBundle,
        skill_executors: Mapping[str, WorkExecutor] | None = None,
    ) -> None:
        self._agents = dict(agents)
        self._registry = registry
        self._skill_executors = dict(skill_executors or {})

    async def __call__(self, context: AgentContextView) -> AgentResult:
        item = context.work_item
        if item.control_mode is not ControlMode.DELEGATED:
            return self._failure(item, "AGENT_REQUIRES_DELEGATED_WORK")
        if item.effect is not CapabilityEffect.READ:
            return self._failure(item, "AGENT_WRITE_REQUIRES_WORKFLOW")
        if item.skill_hint is not None:
            executor = self._skill_executors.get(item.skill_hint)
            if executor is None:
                return self._failure(item, "SKILL_EXECUTOR_NOT_REGISTERED")
            return await executor(context)
        unavailable_skills = tuple(
            skill_id for skill_id in item.allowed_skills
            if skill_id not in self._skill_executors
        )
        if unavailable_skills:
            return self._failure(item, "SKILL_EXECUTOR_NOT_REGISTERED")

        agent_type = _TARGET_AGENT_TYPE[item.owner_agent]
        agent = self._agents.get(agent_type)
        if agent is None:
            return self._failure(item, "DOMAIN_AGENT_NOT_REGISTERED")
        skill_results: list[AgentResult] = []
        request = Request(
            message=context.current_message,
            user_id=str(context.trusted_context.get("user_id") or ""),
            conv_id=str(context.trusted_context.get("conversation_id") or context.trusted_context.get("conv_id") or ""),
            context=_context_payload(context),
            entities={
                argument.name: [str(argument.value)]
                for argument in item.arguments
            },
            assigned_task=TaskSpec(
                task_id=item.work_item_id,
                owner=agent_type,
                objective=item.objective,
                risk=_task_risk(item.risk),
                success_criteria=tuple(
                    f"produce verified requirement {requirement}"
                    for requirement in item.requirement_ids
                ),
                evidence_spans=(context.current_message,),
                depends_on=item.dependencies,
                effect=TaskEffect.READ_ONLY,
                requirement_ids=item.requirement_ids,
            ),
            request_id=str(context.trusted_context.get("request_id") or ""),
            identity_metadata=dict(context.trusted_context),
            bundle_version=str(context.trusted_context.get("bundle_version") or "unversioned"),
            allowed_tool_ids=item.allowed_tools,
            react_capabilities=self._react_capabilities(context, skill_results),
        )
        response = await agent.handle(request)
        return _adapt_response(item, response, self.version, tuple(skill_results))

    def _react_capabilities(
        self,
        context: AgentContextView,
        observed_results: list[AgentResult],
    ) -> tuple[ReActCapability, ...]:
        capabilities = []
        for skill_id in context.work_item.allowed_skills:
            definition = self._registry.skill(skill_id)
            executor = self._skill_executors.get(skill_id)
            if executor is None:
                raise ValueError(f"Skill executor is not registered: {skill_id}")
            properties = {
                name: {"type": "string"}
                for name in (*definition.required_arguments, *definition.optional_arguments)
            }

            async def execute(arguments, execution_context, call_id, *, _id=skill_id, _definition=definition, _executor=executor):
                allowed = set((*_definition.required_arguments, *_definition.optional_arguments))
                if set(arguments) - allowed:
                    return ToolResult(
                        False, None, _id, error="skill arguments exceed Registry schema",
                        call_id=call_id, status=ToolCallStatus.DENIED.value,
                    )
                merged = {
                    argument.name: argument.value
                    for argument in context.work_item.arguments
                    if argument.name in allowed
                }
                merged.update(arguments)
                if set(_definition.required_arguments) - set(merged):
                    return ToolResult(
                        False, None, _id, error="skill required arguments are missing",
                        call_id=call_id, status=ToolCallStatus.ERROR.value,
                    )
                derived = replace(
                    context.work_item,
                    allowed_tools=_definition.allowed_tool_ids,
                    allowed_skills=(_id,),
                    arguments=tuple(
                        ArgumentValue.create(name, value)
                        for name, value in sorted(merged.items())
                    ),
                    requirement_ids=_definition.requirement_ids,
                    skill_hint=_id,
                )
                result = await _executor(replace(context, work_item=derived))
                observed_results.append(result)
                data = {
                    "status": result.status.value,
                    "facts": {
                        fact.requirement_id: json.loads(fact.value_json)
                        for fact in result.facts
                    },
                    "response": result.candidate_response,
                }
                return ToolResult(
                    result.status is AgentResultStatus.SUCCEEDED,
                    data,
                    _id,
                    error=(None if result.status is AgentResultStatus.SUCCEEDED else result.reason_code),
                    call_id=call_id,
                    status=(
                        ToolCallStatus.SUCCESS.value
                        if result.status is AgentResultStatus.SUCCEEDED
                        else ToolCallStatus.ERROR.value
                    ),
                    output_for_model=json.dumps(data, ensure_ascii=False, sort_keys=True),
                    effect_status=ToolEffectStatus.NONE.value,
                    authority=f"skill:{_id}",
                    output_schema_version="agent-result-v1",
                )

            capabilities.append(ReActCapability(
                skill_id,
                definition.objective,
                {
                    "type": "object",
                    "properties": properties,
                    "required": list(definition.required_arguments),
                    "additionalProperties": False,
                },
                execute,
            ))
        return tuple(capabilities)

    def _failure(self, item, reason: str) -> AgentResult:
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.TERMINAL_FAILURE,
            reason,
            self.version,
        )


def _adapt_response(
    item,
    response,
    producer_version: str,
    skill_results: tuple[AgentResult, ...] = (),
) -> AgentResult:
    status = {
        "waiting_approval": AgentResultStatus.WAITING_APPROVAL,
        "blocked": AgentResultStatus.BLOCKED,
        "tool_error": AgentResultStatus.RETRYABLE_FAILURE,
        "max_steps": AgentResultStatus.TERMINAL_FAILURE,
    }.get(response.react_status)
    tool_facts = tuple(
        _fact_from_tool_result(item, result)
        for result in response.tool_results
        if result.success and result.authority in item.requirement_ids
    )
    facts = _merge_facts(tool_facts, tuple(
        fact for result in skill_results for fact in result.facts
        if fact.requirement_id in item.requirement_ids
    ))
    evidence_refs = tuple(dict.fromkeys((
        *(ref for result in skill_results for ref in result.evidence_refs),
        *(
        str(result.receipt_id or result.call_id)
        for result in response.tool_results
        if str(result.receipt_id or result.call_id).strip()
        ),
    )))
    if status is None:
        missing = set(item.requirement_ids).difference(
            fact.requirement_id for fact in facts
        )
        status = (
            AgentResultStatus.SUCCEEDED
            if response.success and not missing
            else AgentResultStatus.TERMINAL_FAILURE
        )
    retryable = status is AgentResultStatus.RETRYABLE_FAILURE
    return AgentResult(
        item.work_item_id,
        item.owner_agent,
        status,
        (
            "REACT_REQUIREMENTS_SATISFIED"
            if status is AgentResultStatus.SUCCEEDED
            else f"REACT_{str(response.react_status or 'FAILED').upper()}"
        ),
        producer_version,
        facts=facts,
        evidence_refs=evidence_refs,
        candidate_response=str(response.content or "").strip() or None,
        retryable=retryable,
    )


def _fact_from_tool_result(item, result) -> FactRecord:
    authority = str(result.authority)
    source_kind = (
        FactSourceKind.KNOWLEDGE_ASSERTED
        if authority.startswith("knowledge.")
        else FactSourceKind.MEDIA_OBSERVED
        if authority.startswith("media.")
        else FactSourceKind.VERIFIED_STATE
    )
    return FactRecord(
        item.aggregate_ref or f"work-item:{item.work_item_id}",
        authority,
        json.dumps(
            result.data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        source_kind,
        str(result.receipt_id or result.call_id),
        result.tool_name,
        str(result.output_schema_version or "tool-output-v1"),
        datetime.now(timezone.utc),
    )


def _context_payload(context: AgentContextView) -> str:
    return json.dumps({
        "verified_facts": [{
            "requirement_id": fact.requirement_id,
            "value": json.loads(fact.value_json),
            "source_ref": fact.source_ref,
            "producer_version": fact.producer_version,
        } for fact in context.verified_facts],
        "recent_relevant_turns": list(context.recent_relevant_turns),
        "evidence_refs": list(context.evidence_refs),
    }, ensure_ascii=False, sort_keys=True)


def _task_risk(risk: CapabilityRisk) -> TaskRisk:
    if risk in {CapabilityRisk.HIGH, CapabilityRisk.CRITICAL}:
        return TaskRisk.HIGH
    if risk is CapabilityRisk.MEDIUM:
        return TaskRisk.MEDIUM
    return TaskRisk.LOW


def _merge_facts(*groups: tuple[FactRecord, ...]) -> tuple[FactRecord, ...]:
    merged = {}
    for fact in (fact for group in groups for fact in group):
        key = (
            fact.subject_ref,
            fact.requirement_id,
            fact.source_ref,
            fact.producer_id,
            fact.producer_version,
        )
        prior = merged.setdefault(key, fact)
        if prior.value_json != fact.value_json:
            raise ValueError("one Skill evidence identity produced conflicting facts")
    return tuple(merged.values())
