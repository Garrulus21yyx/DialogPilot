"""Capability-scoped fail-closed safety gates for evaluation and runtime flags."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SafetyInvariant(str, Enum):
    CROSS_TENANT_ACCESS = "CROSS_TENANT_ACCESS"
    UNAUTHORIZED_TOOL = "UNAUTHORIZED_TOOL"
    DUPLICATE_SIDE_EFFECT = "DUPLICATE_SIDE_EFFECT"
    STALE_RESUME = "STALE_RESUME"
    DUPLICATE_SIGNAL_CONSUMPTION = "DUPLICATE_SIGNAL_CONSUMPTION"
    MEDIA_OVERRIDES_BUSINESS_AUTHORITY = "MEDIA_OVERRIDES_BUSINESS_AUTHORITY"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"


@dataclass(frozen=True)
class SafetyObservation:
    capability_id: str
    invariant: SafetyInvariant
    passed: bool
    evidence_ref: str

    def __post_init__(self) -> None:
        if not self.capability_id.strip() or not self.evidence_ref.strip():
            raise ValueError("safety observation identity is required")


@dataclass(frozen=True)
class CapabilityGateDecision:
    capability_id: str
    enabled: bool
    failed_invariants: tuple[SafetyInvariant, ...]
    evidence_refs: tuple[str, ...]


class CapabilitySafetyGate:
    version = "capability-safety-gate-v1"

    def evaluate(
        self,
        capability_ids: tuple[str, ...],
        observations: tuple[SafetyObservation, ...],
    ) -> tuple[CapabilityGateDecision, ...]:
        unknown = {item.capability_id for item in observations}.difference(capability_ids)
        if unknown:
            raise ValueError(f"safety observations reference unknown capabilities: {sorted(unknown)}")
        decisions = []
        for capability_id in capability_ids:
            scoped = tuple(
                item for item in observations if item.capability_id == capability_id
            )
            failures = tuple(dict.fromkeys(
                item.invariant for item in scoped if not item.passed
            ))
            decisions.append(CapabilityGateDecision(
                capability_id,
                not failures,
                failures,
                tuple(item.evidence_ref for item in scoped if not item.passed),
            ))
        return tuple(decisions)

