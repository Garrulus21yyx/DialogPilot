from __future__ import annotations

from dataclasses import replace
import math

import pytest

from application.capability_contract import (
    CapabilityContractError,
    CapabilityDecision,
    CapabilityDisposition,
    CapabilityName,
    CapabilityOutcome,
    CapabilityTrace,
    InvocationCompliance,
    assess_capability_invocation,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def decision(
    disposition: CapabilityDisposition,
    *,
    capability: CapabilityName = CapabilityName.KNOWLEDGE_RAG,
) -> CapabilityDecision:
    return CapabilityDecision(
        request_id="request-1",
        span_id="turn-understanding",
        capability=capability,
        disposition=disposition,
        reason_code="STATIC_POLICY_EVIDENCE_REQUIRED",
        input_fingerprint=SHA_A,
        policy_version="capability-policy-v1",
    )


def trace(
    outcome: CapabilityOutcome,
    planned: CapabilityDecision | None = None,
) -> CapabilityTrace:
    planned = planned or decision(CapabilityDisposition.REQUIRED)
    produced = outcome in {
        CapabilityOutcome.INVOKED,
        CapabilityOutcome.REUSED,
        CapabilityOutcome.DEGRADED,
    }
    return CapabilityTrace(
        request_id="request-1",
        span_id="turn-understanding",
        capability=CapabilityName.KNOWLEDGE_RAG,
        decision_id=planned.decision_id,
        outcome=outcome,
        reason_code="POLICY_APPLIED",
        outcome_code="EVIDENCE_READY" if produced else outcome.value,
        input_fingerprint=SHA_A,
        output_fingerprint=SHA_B if produced else None,
        producer_version="knowledge-retriever-v1",
        policy_version="capability-policy-v1",
        latency_ms=0 if outcome is CapabilityOutcome.SKIPPED else 12.5,
        token_usage=0,
        attempt=1,
        artifact_refs=("evidence-pack:1",) if produced else (),
        evidence_refs=("knowledge-span:1",) if produced else (),
        model_version="bge-m3-pinned" if produced else "",
        index_generation="knowledge-generation-1" if produced else "",
    )


@pytest.mark.parametrize("disposition", tuple(CapabilityDisposition))
@pytest.mark.parametrize("outcome", tuple(CapabilityOutcome))
def test_invocation_algebra_is_closed_and_separate_from_runtime_outcome(
    disposition: CapabilityDisposition,
    outcome: CapabilityOutcome,
) -> None:
    planned = decision(disposition)
    assessment = assess_capability_invocation(planned, trace(outcome, planned))
    active = outcome is not CapabilityOutcome.SKIPPED
    expected_violation = (
        disposition is CapabilityDisposition.REQUIRED and not active
    ) or (
        disposition in {
            CapabilityDisposition.FORBIDDEN,
            CapabilityDisposition.NOT_APPLICABLE,
        } and active
    )
    assert assessment.compliance is (
        InvocationCompliance.VIOLATION
        if expected_violation else InvocationCompliance.COMPLIANT
    )
    assert assessment.runtime_outcome is outcome


def test_required_failed_call_satisfies_trigger_but_keeps_failure_visible() -> None:
    assessment = assess_capability_invocation(
        decision(CapabilityDisposition.REQUIRED),
        trace(CapabilityOutcome.FAILED),
    )
    assert assessment.compliance is InvocationCompliance.COMPLIANT
    assert assessment.runtime_outcome is CapabilityOutcome.FAILED


def test_forbidden_reuse_is_use_and_is_rejected() -> None:
    assessment = assess_capability_invocation(
        decision(CapabilityDisposition.FORBIDDEN),
        trace(
            CapabilityOutcome.REUSED,
            decision(CapabilityDisposition.FORBIDDEN),
        ),
    )
    assert assessment.compliance is InvocationCompliance.VIOLATION
    assert assessment.reason_code == "FORBIDDEN_CAPABILITY_USED"


def test_trace_cannot_rewrite_decision_identity() -> None:
    with pytest.raises(CapabilityContractError, match="does not match decision"):
        assess_capability_invocation(
            decision(CapabilityDisposition.REQUIRED),
            replace(trace(CapabilityOutcome.INVOKED), input_fingerprint=SHA_B),
        )


def test_one_trace_cannot_satisfy_a_contradictory_decision() -> None:
    required = decision(CapabilityDisposition.REQUIRED)
    forbidden = decision(CapabilityDisposition.FORBIDDEN)
    observed = trace(CapabilityOutcome.INVOKED, required)
    with pytest.raises(CapabilityContractError, match="does not match decision"):
        assess_capability_invocation(forbidden, observed)


def test_skipped_trace_cannot_claim_cost_or_artifacts() -> None:
    with pytest.raises(CapabilityContractError, match="cannot claim work"):
        replace(trace(CapabilityOutcome.SKIPPED), latency_ms=0.1)
    with pytest.raises(CapabilityContractError, match="cannot claim work"):
        replace(
            trace(CapabilityOutcome.SKIPPED),
            output_fingerprint=SHA_B,
            artifact_refs=("fabricated",),
        )


def test_produced_trace_requires_scoreable_artifact() -> None:
    with pytest.raises(CapabilityContractError, match="requires an output"):
        replace(trace(CapabilityOutcome.INVOKED), artifact_refs=())


def test_unknown_capability_fails_closed() -> None:
    with pytest.raises(CapabilityContractError, match="supported CapabilityName"):
        CapabilityDecision(
            request_id="request-1",
            span_id="turn-understanding",
            capability="SURPRISE_PLUGIN",  # type: ignore[arg-type]
            disposition=CapabilityDisposition.ALLOWED,
            reason_code="UNKNOWN",
            input_fingerprint=SHA_A,
            policy_version="capability-policy-v1",
        )


def test_decision_identity_binds_policy_disposition_and_reason() -> None:
    baseline = decision(CapabilityDisposition.REQUIRED)
    assert baseline.decision_id != replace(
        baseline, disposition=CapabilityDisposition.ALLOWED,
    ).decision_id
    assert baseline.decision_id != replace(
        baseline, reason_code="DIFFERENT_REASON",
    ).decision_id


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "1"])
def test_latency_requires_a_finite_number(value: object) -> None:
    with pytest.raises(CapabilityContractError, match="latency_ms"):
        replace(trace(CapabilityOutcome.INVOKED), latency_ms=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("field,value", [("token_usage", 1.5), ("token_usage", True), ("attempt", 1.5), ("attempt", True)])
def test_count_fields_require_non_bool_integers(field: str, value: object) -> None:
    with pytest.raises(CapabilityContractError, match=field):
        replace(trace(CapabilityOutcome.INVOKED), **{field: value})
