"""Planning and observation contracts for optional production capabilities.

``CapabilityDecision`` is the policy-owned statement of what may run for one
turn span.  ``CapabilityTrace`` is an immutable observation emitted by the
production owner after that decision.  Keeping them separate prevents an
evaluator (or a trace producer) from retroactively authorizing an invocation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum


class CapabilityContractError(ValueError):
    """A capability decision/trace is incomplete or internally inconsistent."""


class CapabilityName(str, Enum):
    TURN_STATE_LOAD = "TURN_STATE_LOAD"
    FLOW_ACTION_REGISTRY_LOAD = "FLOW_ACTION_REGISTRY_LOAD"
    DETERMINISTIC_UNDERSTANDING = "DETERMINISTIC_UNDERSTANDING"
    LEGACY_INTENT_ROUTER = "LEGACY_INTENT_ROUTER"
    ENCODER_ROUTER = "ENCODER_ROUTER"
    LLM_COMMAND_ROUTER = "LLM_COMMAND_ROUTER"
    ROUTE_POLICY_VALIDATION = "ROUTE_POLICY_VALIDATION"
    KNOWLEDGE_RAG = "KNOWLEDGE_RAG"
    MEMORY_RAG = "MEMORY_RAG"
    ROUTING_MEDIA_PROBE = "ROUTING_MEDIA_PROBE"
    TASK_MEDIA = "TASK_MEDIA"
    AGENT = "AGENT"
    BUSINESS_TOOL = "BUSINESS_TOOL"
    SYNTHESIZER = "SYNTHESIZER"
    VERIFIER = "VERIFIER"
    HANDOFF_DRAFT = "HANDOFF_DRAFT"
    HANDOFF_WRITE = "HANDOFF_WRITE"


class CapabilityDisposition(str, Enum):
    REQUIRED = "REQUIRED"
    ALLOWED = "ALLOWED"
    FORBIDDEN = "FORBIDDEN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class CapabilityOutcome(str, Enum):
    INVOKED = "INVOKED"
    REUSED = "REUSED"
    SKIPPED = "SKIPPED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class InvocationCompliance(str, Enum):
    COMPLIANT = "COMPLIANT"
    VIOLATION = "VIOLATION"


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ACTIVE_OUTCOMES = frozenset({
    CapabilityOutcome.INVOKED,
    CapabilityOutcome.REUSED,
    CapabilityOutcome.DEGRADED,
    CapabilityOutcome.FAILED,
})


@dataclass(frozen=True)
class CapabilityDecision:
    """Policy-owned invocation algebra for one request span."""

    request_id: str
    span_id: str
    capability: CapabilityName
    disposition: CapabilityDisposition
    reason_code: str
    input_fingerprint: str
    policy_version: str

    def __post_init__(self) -> None:
        _required(self.request_id, "request_id")
        _required(self.span_id, "span_id")
        _required(self.reason_code, "reason_code")
        _required(self.policy_version, "policy_version")
        _sha256(self.input_fingerprint, "input_fingerprint")
        if not isinstance(self.capability, CapabilityName):
            raise CapabilityContractError("capability must be a supported CapabilityName")
        if not isinstance(self.disposition, CapabilityDisposition):
            raise CapabilityContractError(
                "disposition must be a supported CapabilityDisposition"
            )

    @property
    def decision_id(self) -> str:
        return "capability-decision:v1:" + _fingerprint({
            "request_id": self.request_id,
            "span_id": self.span_id,
            "capability": self.capability.value,
            "disposition": self.disposition.value,
            "reason_code": self.reason_code,
            "input_fingerprint": self.input_fingerprint,
            "policy_version": self.policy_version,
        })


@dataclass(frozen=True)
class CapabilityTrace:
    """Producer-owned observation; it never changes the associated decision."""

    request_id: str
    span_id: str
    capability: CapabilityName
    decision_id: str
    outcome: CapabilityOutcome
    reason_code: str
    outcome_code: str
    input_fingerprint: str
    output_fingerprint: str | None
    producer_version: str
    policy_version: str
    latency_ms: float
    token_usage: int
    attempt: int
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    model_version: str = ""
    index_generation: str = ""

    def __post_init__(self) -> None:
        for field, value in (
            ("request_id", self.request_id),
            ("span_id", self.span_id),
            ("decision_id", self.decision_id),
            ("reason_code", self.reason_code),
            ("outcome_code", self.outcome_code),
            ("producer_version", self.producer_version),
            ("policy_version", self.policy_version),
        ):
            _required(value, field)
        _sha256(self.input_fingerprint, "input_fingerprint")
        if not isinstance(self.capability, CapabilityName):
            raise CapabilityContractError("capability must be a supported CapabilityName")
        if not isinstance(self.outcome, CapabilityOutcome):
            raise CapabilityContractError("outcome must be a supported CapabilityOutcome")
        if self.output_fingerprint is not None:
            _sha256(self.output_fingerprint, "output_fingerprint")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, (int, float))
            or not math.isfinite(self.latency_ms)
            or self.latency_ms < 0
        ):
            raise CapabilityContractError(
                "latency_ms must be a finite non-negative number"
            )
        if (
            isinstance(self.token_usage, bool)
            or not isinstance(self.token_usage, int)
            or self.token_usage < 0
        ):
            raise CapabilityContractError(
                "token_usage must be a non-negative integer"
            )
        if (
            isinstance(self.attempt, bool)
            or not isinstance(self.attempt, int)
            or self.attempt < 1
        ):
            raise CapabilityContractError(
                "attempt must be a positive integer"
            )
        _canonical_string_set(self, "artifact_refs")
        _canonical_string_set(self, "evidence_refs")

        produced = self.outcome in {
            CapabilityOutcome.INVOKED,
            CapabilityOutcome.REUSED,
            CapabilityOutcome.DEGRADED,
        }
        if produced and (
            self.output_fingerprint is None or not self.artifact_refs
        ):
            raise CapabilityContractError(
                f"{self.outcome.value} requires an output fingerprint and artifact ref"
            )
        if self.outcome is CapabilityOutcome.SKIPPED and (
            self.output_fingerprint is not None
            or self.artifact_refs
            or self.evidence_refs
            or self.latency_ms != 0
            or self.token_usage != 0
        ):
            raise CapabilityContractError("SKIPPED cannot claim work or artifacts")
        if self.outcome is CapabilityOutcome.FAILED and (
            self.artifact_refs or self.evidence_refs
        ):
            raise CapabilityContractError("FAILED cannot publish usable artifacts")

    @property
    def invocation_id(self) -> str:
        return "capability-invocation:v1:" + _fingerprint({
            "request_id": self.request_id,
            "span_id": self.span_id,
            "capability": self.capability.value,
            "decision_id": self.decision_id,
            "attempt": self.attempt,
            "input_fingerprint": self.input_fingerprint,
        })


@dataclass(frozen=True)
class CapabilityInvocationAssessment:
    """Trigger-only comparison; artifact and task outcome remain separate scores."""

    compliance: InvocationCompliance
    reason_code: str
    decision_id: str
    invocation_id: str
    runtime_outcome: CapabilityOutcome


def assess_capability_invocation(
    decision: CapabilityDecision,
    trace: CapabilityTrace,
) -> CapabilityInvocationAssessment:
    """Compare planned permission with observed use without hiding runtime failure.

    ``FAILED`` is an attempted invocation, so it can satisfy a REQUIRED trigger
    while still remaining a failed runtime outcome for Artifact/Outcome scoring.
    """

    expected_identity = (
        decision.request_id,
        decision.span_id,
        decision.capability,
        decision.decision_id,
        decision.input_fingerprint,
        decision.policy_version,
    )
    observed_identity = (
        trace.request_id,
        trace.span_id,
        trace.capability,
        trace.decision_id,
        trace.input_fingerprint,
        trace.policy_version,
    )
    if expected_identity != observed_identity:
        raise CapabilityContractError(
            "trace request/span/capability/input/policy does not match decision"
        )

    active = trace.outcome in _ACTIVE_OUTCOMES
    violation = (
        decision.disposition is CapabilityDisposition.REQUIRED and not active
    ) or (
        decision.disposition in {
            CapabilityDisposition.FORBIDDEN,
            CapabilityDisposition.NOT_APPLICABLE,
        } and active
    )
    if violation:
        reason = (
            "REQUIRED_CAPABILITY_SKIPPED"
            if decision.disposition is CapabilityDisposition.REQUIRED
            else f"{decision.disposition.value}_CAPABILITY_USED"
        )
        compliance = InvocationCompliance.VIOLATION
    else:
        reason = "CAPABILITY_INVOCATION_COMPLIANT"
        compliance = InvocationCompliance.COMPLIANT
    return CapabilityInvocationAssessment(
        compliance=compliance,
        reason_code=reason,
        decision_id=decision.decision_id,
        invocation_id=trace.invocation_id,
        runtime_outcome=trace.outcome,
    )


def _required(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise CapabilityContractError(f"{field} is required")
    return normalized


def _sha256(value: object, field: str) -> str:
    normalized = _required(value, field)
    if not _SHA256.fullmatch(normalized):
        raise CapabilityContractError(f"{field} must be a lowercase SHA-256")
    return normalized


def _unique_nonblank(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple):
        raise CapabilityContractError(f"{field} must be an immutable tuple")
    if any(not str(value).strip() for value in values):
        raise CapabilityContractError(f"{field} must not contain blank values")
    if len(values) != len(set(values)):
        raise CapabilityContractError(f"{field} must be unique")


def _canonical_string_set(instance: object, attribute: str) -> None:
    values = getattr(instance, attribute)
    _unique_nonblank(values, attribute)
    object.__setattr__(instance, attribute, tuple(sorted(values)))


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
