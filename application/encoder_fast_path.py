"""Precision-first intent encoder acceptance and bounded evidence routing."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from application.capability_registry import CapabilityEffect, CapabilityRegistryBundle
from application.conversation_state import ConversationState
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
)
from application.work_item import ArgumentValue


class EncoderContractError(ValueError):
    pass


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: str
    score: float

    def __post_init__(self) -> None:
        if not self.candidate_id.strip() or not 0.0 <= self.score <= 1.0:
            raise EncoderContractError("encoder candidate is invalid")


@dataclass(frozen=True)
class IntentEncoderOutput:
    domains: tuple[RankedCandidate, ...]
    capabilities: tuple[RankedCandidate, ...]
    entities: tuple[tuple[str, object], ...]
    boundary_score: float
    multi_intent: bool
    out_of_distribution: bool
    missing_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.boundary_score <= 1.0:
            raise EncoderContractError("boundary score must be in [0,1]")
        names = tuple(name for name, _ in self.entities)
        if len(names) != len(set(names)) or any(not name.strip() for name in names):
            raise EncoderContractError("encoder entities must have unique names")


@dataclass(frozen=True)
class FastPathDecision:
    accepted: bool
    reason_code: str
    proposal: TurnProposal | None = None


class EncoderFastPathPolicy:
    version = "encoder-fast-path-policy-v1"

    def __init__(
        self,
        *,
        capability_thresholds: Mapping[str, float],
        required_arguments: Mapping[str, tuple[str, ...]] | None = None,
        max_boundary_score: float = 0.15,
        minimum_margin: float = 0.1,
    ) -> None:
        self._thresholds = dict(capability_thresholds)
        self._required_arguments = dict(required_arguments or {})
        self._max_boundary = max_boundary_score
        self._minimum_margin = minimum_margin

    def decide(
        self,
        output: IntentEncoderOutput,
        state: ConversationState,
        registry: CapabilityRegistryBundle,
    ) -> FastPathDecision:
        if output.out_of_distribution:
            return FastPathDecision(False, "ENCODER_OOD")
        if output.multi_intent:
            return FastPathDecision(False, "ENCODER_MULTI_INTENT")
        if output.missing_inputs:
            return FastPathDecision(False, "ENCODER_MISSING_INPUT")
        if output.boundary_score > self._max_boundary:
            return FastPathDecision(False, "ENCODER_BOUNDARY")
        if state.pending_interaction or state.pending_approval or state.active_workstreams:
            return FastPathDecision(False, "ENCODER_STATE_CONFLICT")
        if len(output.domains) != 1 or not output.capabilities:
            return FastPathDecision(False, "ENCODER_NOT_UNIQUE")
        top = output.capabilities[0]
        runner_up = (
            output.capabilities[1].score
            if len(output.capabilities) > 1 else 0.0
        )
        threshold = self._thresholds.get(top.candidate_id)
        if threshold is None:
            return FastPathDecision(False, "ENCODER_UNCALIBRATED_SKILL")
        if top.score < threshold or top.score - runner_up < self._minimum_margin:
            return FastPathDecision(False, "ENCODER_LOW_CONFIDENCE")
        try:
            kind, capability_id = top.candidate_id.split(":", 1)
            if kind == "skill":
                skill = registry.skill(capability_id)
                owner = registry.agent(skill.owner_agent)
                effect = skill.effect
                required = skill.required_arguments
            elif kind == "tool":
                tool = registry.tool(capability_id)
                owner = registry.agent(output.domains[0].candidate_id)
                if tool.tool_id not in owner.allowed_tool_ids:
                    return FastPathDecision(False, "ENCODER_UNSUPPORTED_CAPABILITY")
                effect = tool.effect
                required = self._required_arguments.get(top.candidate_id, ())
            else:
                return FastPathDecision(False, "ENCODER_UNSUPPORTED_CAPABILITY")
        except Exception:
            return FastPathDecision(False, "ENCODER_UNSUPPORTED_CAPABILITY")
        if effect is not CapabilityEffect.READ:
            return FastPathDecision(False, "ENCODER_WRITE_DEFERRED")
        entities = dict(output.entities)
        if set(required).difference(entities):
            return FastPathDecision(False, "ENCODER_REQUIRED_ARGUMENT_MISSING")
        allowed_arguments = (
            set(skill.required_arguments).union(skill.optional_arguments)
            if kind == "skill" else set(required)
        )
        arguments = tuple(
            ArgumentValue.create(name, value)
            for name, value in output.entities
            if name in allowed_arguments
        )
        command = (
            CommandProposal(
                command_id=f"encoder:skill:{skill.skill_id}",
                kind=CommandKind.RUN_SKILL,
                target_agent=owner.agent_id,
                objective=skill.objective,
                arguments=arguments,
                requirement_ids=skill.requirement_ids,
                skill_id=skill.skill_id,
            )
            if kind == "skill" else CommandProposal(
                command_id=f"encoder:tool:{tool.tool_id}",
                kind=CommandKind.DIRECT_TOOL,
                target_agent=owner.agent_id,
                objective=f"Execute calibrated read capability {tool.tool_id}",
                arguments=arguments,
                requirement_ids=(tool.authority,),
                tool_id=tool.tool_id,
            )
        )
        return FastPathDecision(
            True,
            "ENCODER_FAST_PATH_ACCEPTED",
            TurnProposal(
                ProposalDisposition.RESOLVED,
                (command,),
                "ENCODER_FAST_PATH_ACCEPTED",
            ),
        )


class MediaEvidenceAction(str, Enum):
    REUSE = "REUSE"
    OCR = "OCR"
    VLM = "VLM"


@dataclass(frozen=True)
class MediaAssetSignal:
    asset_id: str
    media_type: str
    reusable_evidence_ref: str | None = None
    text_extraction_sufficient: bool = False
    semantic_visual_reasoning_required: bool = False

    def __post_init__(self) -> None:
        if not self.asset_id.strip() or not self.media_type.strip():
            raise EncoderContractError("media asset identity is required")


@dataclass(frozen=True)
class MediaEvidenceStep:
    asset_id: str
    action: MediaEvidenceAction
    evidence_ref: str | None = None


@dataclass(frozen=True)
class UnderstandingEvidencePlan:
    request_memory: bool
    memory_reason_code: str
    media_steps: tuple[MediaEvidenceStep, ...]
    next_memory_attempt: int


class UnderstandingEvidencePolicy:
    """Plan evidence before routing; memory can supplement understanding once."""

    version = "understanding-evidence-policy-v1"

    def plan(
        self,
        *,
        unresolved_historical_reference: bool,
        memory_attempt: int,
        media_assets: tuple[MediaAssetSignal, ...],
    ) -> UnderstandingEvidencePlan:
        if memory_attempt < 0 or memory_attempt > 1:
            raise EncoderContractError("understanding memory attempt is outside the closed budget")
        request_memory = unresolved_historical_reference and memory_attempt == 0
        steps = []
        for asset in media_assets:
            if asset.reusable_evidence_ref:
                steps.append(MediaEvidenceStep(
                    asset.asset_id, MediaEvidenceAction.REUSE, asset.reusable_evidence_ref,
                ))
            elif asset.semantic_visual_reasoning_required:
                steps.append(MediaEvidenceStep(asset.asset_id, MediaEvidenceAction.VLM))
            elif asset.text_extraction_sufficient:
                steps.append(MediaEvidenceStep(asset.asset_id, MediaEvidenceAction.OCR))
            else:
                steps.append(MediaEvidenceStep(asset.asset_id, MediaEvidenceAction.VLM))
        return UnderstandingEvidencePlan(
            request_memory,
            "HISTORICAL_REFERENCE_UNRESOLVED" if request_memory else "MEMORY_NOT_REQUIRED",
            tuple(steps),
            1 if request_memory else memory_attempt,
        )
