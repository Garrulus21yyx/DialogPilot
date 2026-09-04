"""Thin adapter from Target WorkItems to the existing bounded ReAct agents."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping

from agents.agent_orchestrator import BaseAgent, Request
from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk, TaskSpec
from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.orchestration_runtime import AgentContextView, WorkExecutor
from application.work_item import ControlMode


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
        skill_executors: Mapping[str, WorkExecutor] | None = None,
    ) -> None:
        self._agents = dict(agents)
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

        agent_type = _TARGET_AGENT_TYPE[item.owner_agent]
        agent = self._agents.get(agent_type)
        if agent is None:
            return self._failure(item, "DOMAIN_AGENT_NOT_REGISTERED")
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
        )
        response = await agent.handle(request)
        return _adapt_response(item, response, self.version)

    def _failure(self, item, reason: str) -> AgentResult:
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.TERMINAL_FAILURE,
            reason,
            self.version,
        )


def _adapt_response(item, response, producer_version: str) -> AgentResult:
    status = {
        "waiting_approval": AgentResultStatus.WAITING_APPROVAL,
        "blocked": AgentResultStatus.BLOCKED,
        "tool_error": AgentResultStatus.RETRYABLE_FAILURE,
        "max_steps": AgentResultStatus.TERMINAL_FAILURE,
    }.get(response.react_status)
    facts = tuple(
        _fact_from_tool_result(item, result)
        for result in response.tool_results
        if result.success and result.authority in item.requirement_ids
    )
    evidence_refs = tuple(dict.fromkeys(
        str(result.receipt_id or result.call_id)
        for result in response.tool_results
        if str(result.receipt_id or result.call_id).strip()
    ))
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
