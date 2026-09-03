"""Target v1 six-layer evaluation funnel with capability-scoped hard gates."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from application.capability_safety import (
    CapabilityGateDecision,
    CapabilitySafetyGate,
    SafetyInvariant,
    SafetyObservation,
)


class TargetEvalContractError(ValueError):
    pass


class FunnelLayer(str, Enum):
    TRIGGER = "TRIGGER"
    ARTIFACT = "ARTIFACT"
    CONSUMPTION = "CONSUMPTION"
    STATE_SIDE_EFFECT = "STATE_SIDE_EFFECT"
    OUTCOME = "OUTCOME"
    COST = "COST"


@dataclass(frozen=True)
class LayerCheck:
    layer: FunnelLayer
    passed: bool
    reason_codes: tuple[str, ...]
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class TargetEvalExpectation:
    case_id: str
    capability_id: str
    route_mode: str
    owner_ids: tuple[str, ...]
    tool_calls: tuple[str, ...]
    terminal_state: str
    outcome: str
    verifier_status: str
    max_provider_calls: int
    max_work_items: int
    required_safety_invariants: tuple[SafetyInvariant, ...] = ()

    def __post_init__(self) -> None:
        values = (
            self.case_id, self.capability_id, self.route_mode,
            self.terminal_state, self.outcome, self.verifier_status,
        )
        if any(not value.strip() for value in values):
            raise TargetEvalContractError("evaluation expectation identity is required")
        if self.max_provider_calls < 0 or self.max_work_items < 0:
            raise TargetEvalContractError("evaluation budgets must be non-negative")


@dataclass(frozen=True)
class TargetEvalObservation:
    invocation_admitted: bool
    route_mode: str
    owner_ids: tuple[str, ...]
    registry_fingerprint: str
    plan_id: str
    work_item_ids: tuple[str, ...]
    consumed_components: tuple[str, ...]
    tool_calls: tuple[str, ...]
    terminal_state: str
    committed_receipt_refs: tuple[str, ...]
    outcome: str
    verifier_status: str
    publication_ref: str
    provider_calls: int
    latency_ms: float
    safety_observations: tuple[SafetyObservation, ...] = ()


@dataclass(frozen=True)
class TargetEvalResult:
    case_id: str
    capability_id: str
    layers: tuple[LayerCheck, ...]
    gate: CapabilityGateDecision

    @property
    def passed(self) -> bool:
        return self.gate.enabled and all(item.passed for item in self.layers)


@dataclass(frozen=True)
class TargetEvalReport:
    results: tuple[TargetEvalResult, ...]

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.results)

    @property
    def failed_case_ids(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.results if not item.passed)

    @property
    def capability_gates(self) -> Mapping[str, bool]:
        values: dict[str, bool] = {}
        for item in self.results:
            values[item.capability_id] = values.get(item.capability_id, True) and item.gate.enabled
        return values


class TargetArchitectureEvaluator:
    version = "target-architecture-evaluator-v1"

    def evaluate(
        self,
        cases: tuple[tuple[TargetEvalExpectation, TargetEvalObservation], ...],
    ) -> TargetEvalReport:
        if not cases:
            raise TargetEvalContractError("evaluation requires at least one case")
        case_ids = tuple(expected.case_id for expected, _ in cases)
        if len(case_ids) != len(set(case_ids)):
            raise TargetEvalContractError("evaluation case IDs must be unique")
        return TargetEvalReport(tuple(
            self._evaluate_case(expected, observed) for expected, observed in cases
        ))

    @staticmethod
    def _evaluate_case(expected, observed) -> TargetEvalResult:
        trigger = _check(
            FunnelLayer.TRIGGER,
            ((observed.invocation_admitted, "INVOCATION_NOT_ADMITTED"),),
            (f"case:{expected.case_id}:admission",),
        )
        artifact = _check(
            FunnelLayer.ARTIFACT,
            (
                (observed.route_mode == expected.route_mode, "ROUTE_MODE_MISMATCH"),
                (observed.owner_ids == expected.owner_ids, "OWNER_MISMATCH"),
                (bool(observed.registry_fingerprint), "REGISTRY_FINGERPRINT_MISSING"),
                (bool(observed.plan_id), "PLAN_ID_MISSING"),
            ),
            tuple(filter(None, (observed.registry_fingerprint, observed.plan_id))),
        )
        consumption = _check(
            FunnelLayer.CONSUMPTION,
            (
                (observed.tool_calls == expected.tool_calls, "TOOL_CONSUMPTION_MISMATCH"),
                (len(set(observed.work_item_ids)) == len(observed.work_item_ids),
                 "DUPLICATE_WORK_ITEM"),
            ),
            tuple(f"component:{item}" for item in observed.consumed_components),
        )
        state_effect = _check(
            FunnelLayer.STATE_SIDE_EFFECT,
            ((observed.terminal_state == expected.terminal_state,
              "TERMINAL_STATE_MISMATCH"),),
            observed.committed_receipt_refs or (f"state:{observed.terminal_state}",),
        )
        outcome = _check(
            FunnelLayer.OUTCOME,
            (
                (observed.outcome == expected.outcome, "OUTCOME_MISMATCH"),
                (observed.verifier_status == expected.verifier_status,
                 "VERIFIER_STATUS_MISMATCH"),
                (bool(observed.publication_ref), "PUBLICATION_MISSING"),
            ),
            tuple(filter(None, (observed.publication_ref,))),
        )
        cost = _check(
            FunnelLayer.COST,
            (
                (observed.provider_calls <= expected.max_provider_calls,
                 "PROVIDER_CALL_BUDGET_EXCEEDED"),
                (len(observed.work_item_ids) <= expected.max_work_items,
                 "WORK_ITEM_BUDGET_EXCEEDED"),
                (observed.latency_ms >= 0, "LATENCY_INVALID"),
            ),
            (f"latency-ms:{observed.latency_ms:.3f}",),
        )
        gate = CapabilitySafetyGate().evaluate(
            (expected.capability_id,),
            observed.safety_observations,
            required_invariants={
                expected.capability_id: expected.required_safety_invariants,
            },
        )[0]
        return TargetEvalResult(
            expected.case_id,
            expected.capability_id,
            (trigger, artifact, consumption, state_effect, outcome, cost),
            gate,
        )


def _check(layer, predicates, evidence_refs):
    reasons = tuple(reason for passed, reason in predicates if not passed)
    return LayerCheck(layer, not reasons, reasons, tuple(evidence_refs))
