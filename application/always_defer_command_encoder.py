"""Explicit Encoder-deferred correctness baseline for command understanding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from application.route_policy_v2 import FlowActionRegistry
from application.selective_command_producer import (
    EncoderCommandCandidate,
    EncoderCommandDecision,
    EncoderDisposition,
)
from application.turn_state import TurnStateSnapshot


@dataclass(frozen=True)
class AlwaysDeferCommandEncoder:
    """Send every semantic decision to the structured LLM correctness path."""

    candidates: tuple[EncoderCommandCandidate, ...] = ()
    artifact_version: str = "always-defer-command-encoder-v1"

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple):
            raise ValueError("always-defer candidates must be an immutable tuple")
        if not self.artifact_version.strip():
            raise ValueError("always-defer artifact_version is required")

    async def decide(
        self,
        message: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        *,
        history: tuple[Mapping[str, str], ...] = (),
    ) -> EncoderCommandDecision:
        del message, state, registry, history
        return EncoderCommandDecision(
            disposition=EncoderDisposition.DEFER,
            candidates=self.candidates,
            selected_candidate_id=None,
            artifact_version=self.artifact_version,
            calibration_version="not-applicable",
            reason_code="CORRECTNESS_BASELINE_ALWAYS_DEFER",
        )
