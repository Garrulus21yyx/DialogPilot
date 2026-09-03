"""Selective command production without legacy intent voting."""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from agents.orchestration_contracts import TaskEffect, TaskRisk
from application.route_policy_v2 import (
    ApprovalPolicy,
    FlowActionRegistry,
    RoutePolicyError,
)
from application.turn_state import FlowDefinitionRef, TurnStateSnapshot
from application.turn_understanding import (
    CommandKind,
    CommandProposal,
    UnderstandingError,
    UnderstandingResult,
    UnderstandingSource,
    UnderstandingStatus,
)


class EncoderDisposition(str, Enum):
    ACCEPT = "ACCEPT"
    DEFER = "DEFER"


@dataclass(frozen=True)
class EncoderCommandCandidate:
    candidate_id: str
    command_kind: CommandKind
    flow: FlowDefinitionRef | None
    calibrated_accept_probability: float

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("encoder candidate_id is required")
        if not isinstance(self.command_kind, CommandKind):
            raise ValueError("encoder candidate command kind is invalid")
        probability = self.calibrated_accept_probability
        if isinstance(probability, bool) or not 0.0 <= probability <= 1.0:
            raise ValueError("calibrated accept probability must be in [0, 1]")


@dataclass(frozen=True)
class EncoderCommandDecision:
    disposition: EncoderDisposition
    candidates: tuple[EncoderCommandCandidate, ...]
    selected_candidate_id: str | None
    artifact_version: str
    calibration_version: str
    reason_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, EncoderDisposition):
            raise ValueError("encoder disposition is invalid")
        if not isinstance(self.candidates, tuple):
            raise ValueError("encoder candidates must be an immutable tuple")
        candidate_ids = tuple(item.candidate_id for item in self.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("encoder candidates must be unique")
        for value in (
            self.artifact_version,
            self.calibration_version,
            self.reason_code,
        ):
            if not value.strip():
                raise ValueError("encoder decision version and reason are required")
        if self.disposition is EncoderDisposition.ACCEPT:
            if self.selected_candidate_id not in candidate_ids:
                raise ValueError("accepted encoder decision must select one candidate")
        elif self.selected_candidate_id is not None:
            raise ValueError("deferred encoder decision cannot select a candidate")

    @property
    def selected(self) -> EncoderCommandCandidate | None:
        if self.selected_candidate_id is None:
            return None
        return next(
            item
            for item in self.candidates
            if item.candidate_id == self.selected_candidate_id
        )


class EncoderCommandArtifact(Protocol):
    """Loaded encoder plus its calibration artifact."""

    async def decide(
        self,
        message: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        *,
        history: tuple[Mapping[str, str], ...] = (),
    ) -> EncoderCommandDecision: ...


class StructuredLLMCommandProducer(Protocol):
    async def produce(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        candidates: tuple[EncoderCommandCandidate, ...],
        *,
        history: tuple[Mapping[str, str], ...] = (),
        bundle: Any = None,
    ) -> UnderstandingResult: ...


class CommandProducerStage(str, Enum):
    ENCODER = "ENCODER"
    LLM = "LLM"


@dataclass(frozen=True)
class SelectiveCommandOutcome:
    understanding: UnderstandingResult
    encoder_decision: EncoderCommandDecision
    stage: CommandProducerStage
    latency_ms: float


class SelectiveCommandProducer:
    """Use the Encoder only for registry-backed low-risk read paths."""

    def __init__(
        self,
        encoder: EncoderCommandArtifact,
        llm: StructuredLLMCommandProducer,
    ) -> None:
        self._encoder = encoder
        self._llm = llm

    async def understand(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        *,
        history: tuple[Mapping[str, str], ...] = (),
        bundle: Any = None,
    ) -> UnderstandingResult:
        outcome = await self.produce(
            message,
            message_fingerprint,
            state,
            registry,
            history=history,
            bundle=bundle,
        )
        return outcome.understanding

    async def produce(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        *,
        history: tuple[Mapping[str, str], ...] = (),
        bundle: Any = None,
    ) -> SelectiveCommandOutcome:
        started = time.perf_counter()
        decision = await self._encoder.decide(
            message,
            state,
            registry,
            history=history,
        )
        proposal = self._fast_path_proposal(
            decision,
            message_fingerprint,
            state,
            registry,
        )
        if proposal is not None:
            return SelectiveCommandOutcome(
                UnderstandingResult(UnderstandingStatus.RESOLVED, (proposal,)),
                decision,
                CommandProducerStage.ENCODER,
                (time.perf_counter() - started) * 1000.0,
            )

        understanding = await self._llm.produce(
            message,
            message_fingerprint,
            state,
            registry,
            decision.candidates,
            history=history,
            bundle=bundle,
        )
        self._validate_llm_result(understanding, message_fingerprint)
        return SelectiveCommandOutcome(
            understanding,
            decision,
            CommandProducerStage.LLM,
            (time.perf_counter() - started) * 1000.0,
        )

    @staticmethod
    def _fast_path_proposal(
        decision: EncoderCommandDecision,
        message_fingerprint: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> CommandProposal | None:
        candidate = decision.selected
        if decision.disposition is not EncoderDisposition.ACCEPT or candidate is None:
            return None
        if candidate.command_kind not in _FAST_PATH_COMMANDS:
            return None

        source_flow = None
        target_flow = None
        if candidate.command_kind is CommandKind.CONTINUE_FLOW:
            matching = tuple(
                item for item in state.active_flows
                if item.definition == candidate.flow
            )
            if len(matching) != 1:
                return None
            source_flow = matching[0]
        elif candidate.command_kind is CommandKind.START_FLOW:
            if candidate.flow is None:
                return None
            target_flow = candidate.flow
        elif candidate.flow is not None:
            return None

        proposal = CommandProposal(
            kind=candidate.command_kind,
            source=UnderstandingSource.ENCODER,
            message_fingerprint=message_fingerprint,
            producer_version=(
                f"{decision.artifact_version}+{decision.calibration_version}"
            ),
            evidence_refs=(
                f"message:{message_fingerprint}",
                f"encoder-candidate:{candidate.candidate_id}",
            ),
            source_flow=source_flow,
            target_flow=target_flow,
        )
        try:
            if source_flow is not None:
                registry.flow(source_flow.definition)
            if target_flow is not None:
                registry.flow(target_flow)
            action = registry.action_for(proposal)
        except RoutePolicyError:
            return None
        if (
            action.effect is not TaskEffect.READ_ONLY
            or action.risk is not TaskRisk.LOW
            or action.approval is not ApprovalPolicy.USER_COMMAND_SUFFICIENT
        ):
            return None
        return proposal

    @staticmethod
    def _validate_llm_result(
        understanding: UnderstandingResult,
        message_fingerprint: str,
    ) -> None:
        if understanding.status is not UnderstandingStatus.RESOLVED:
            return
        if any(
            command.source is not UnderstandingSource.LLM
            or command.message_fingerprint != message_fingerprint
            for command in understanding.commands
        ):
            raise UnderstandingError(
                "LLM producer returned a command for another source or message"
            )


_FAST_PATH_COMMANDS = frozenset({
    CommandKind.ANSWER_KNOWLEDGE,
    CommandKind.START_FLOW,
    CommandKind.CONTINUE_FLOW,
})
