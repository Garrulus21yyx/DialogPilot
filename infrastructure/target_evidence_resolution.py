"""Registry-governed execution of typed evidence requests from domain workers."""
from __future__ import annotations

from application.agent_result import AgentResultStatus, EvidenceRequest, FactRecord
from application.capability_registry import CapabilityEffect, CapabilityRegistryBundle
from application.orchestration_runtime import AgentContextView, WorkExecutor
from application.work_item import ControlMode, WorkItem


class EvidenceResolutionError(ValueError):
    pass


class TargetEvidenceResolver:
    """Resolve evidence through an allowed atomic read Tool, never free-form text."""

    version = "target-evidence-resolver-v1"

    def __init__(
        self,
        registry: CapabilityRegistryBundle,
        direct_executor: WorkExecutor,
    ) -> None:
        self._registry = registry
        self._direct_executor = direct_executor

    async def __call__(
        self,
        request: EvidenceRequest,
        context: AgentContextView,
    ) -> tuple[FactRecord, ...]:
        requirement = self._registry.requirement(request.requirement_id)
        candidates = tuple(
            provider_id
            for provider_id in request.preferred_providers
            if provider_id in context.work_item.allowed_tools
            and provider_id in requirement.allowed_tools
        )
        if not candidates:
            raise EvidenceResolutionError(
                "evidence request has no provider inside the WorkItem envelope"
            )
        tool = self._registry.tool(candidates[0])
        if tool.effect is not CapabilityEffect.READ:
            raise EvidenceResolutionError("evidence provider must be read-only")
        if tool.authority != request.requirement_id:
            raise EvidenceResolutionError(
                "evidence provider authority differs from the requested requirement"
            )
        evidence_item = WorkItem(
            work_item_id=f"evidence:{context.work_item.work_item_id}:{request.requirement_id}",
            owner_agent=context.work_item.owner_agent,
            objective=f"Resolve evidence for {request.requirement_id}",
            control_mode=ControlMode.DIRECT,
            allowed_tools=(tool.tool_id,),
            allowed_skills=(),
            arguments=request.arguments,
            requirement_ids=(request.requirement_id,),
            dependencies=(),
            effect=tool.effect,
            risk=tool.risk,
            expected_output_schema="agent-result-v1",
            verification_profile=tool.verification_profile,
            state_snapshot_version=context.work_item.state_snapshot_version,
            registry_fingerprint=self._registry.fingerprint,
            timeout_seconds=context.work_item.timeout_seconds,
            max_steps=1,
        )
        result = await self._direct_executor(AgentContextView(
            evidence_item,
            context.current_message,
            context.verified_facts,
            context.recent_relevant_turns,
            context.evidence_refs,
            context.token_budget,
            context.trusted_context,
        ))
        if result.status not in {
            AgentResultStatus.SUCCEEDED,
            AgentResultStatus.PARTIAL,
        }:
            return ()
        facts = tuple(
            fact for fact in result.facts
            if fact.requirement_id == request.requirement_id
        )
        if len(facts) != len(result.facts):
            raise EvidenceResolutionError(
                "evidence provider returned facts outside its requirement"
            )
        return facts
