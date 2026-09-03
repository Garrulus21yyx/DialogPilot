"""JSON-facing contracts shared by the thin component runners."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class EvaluationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_RUN = "NOT_RUN"
    BLOCKED_MISSING_SYSTEM_CAPABILITY = "BLOCKED_MISSING_SYSTEM_CAPABILITY"


@dataclass(frozen=True)
class EvalCase:
    """One state-aware input and its expected component artifact."""

    case_id: str
    message: str
    initial_state: Mapping[str, Any]
    expected: Mapping[str, Any]
    slice: str = "all"

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "message": self.message,
            "initial_state": dict(self.initial_state),
            "expected": dict(self.expected),
            "slice": self.slice,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvalCase":
        return cls(
            case_id=str(value["case_id"]),
            message=str(value["message"]),
            initial_state=dict(value.get("initial_state") or {}),
            expected=dict(value.get("expected") or {}),
            slice=str(value.get("slice") or "all"),
        )


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "detail": dict(self.detail)}


@dataclass(frozen=True)
class CostResult:
    latency_ms: float
    token_usage: int
    invocation_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "latency_ms": self.latency_ms,
            "token_usage": self.token_usage,
            "invocation_count": self.invocation_count,
        }


@dataclass(frozen=True)
class DirectEvaluationResult:
    trigger: CheckResult
    artifact: CheckResult
    consumption: CheckResult
    outcome: CheckResult
    cost: CostResult

    @property
    def passed(self) -> bool:
        return all((
            self.trigger.passed,
            self.artifact.passed,
            self.consumption.passed,
            self.outcome.passed,
        ))


@dataclass(frozen=True)
class RunManifest:
    schema_version: str
    run_id: str
    component: str
    dataset_id: str
    split: str
    dataset_checksum: str
    evaluator_version: str
    adapter_version: str
    case_count: int
    created_at: str
    trial_count: int = 1
    configuration: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "component": self.component,
            "dataset_id": self.dataset_id,
            "split": self.split,
            "dataset_checksum": self.dataset_checksum,
            "evaluator_version": self.evaluator_version,
            "adapter_version": self.adapter_version,
            "case_count": self.case_count,
            "trial_count": self.trial_count,
            "created_at": self.created_at,
            "configuration": dict(self.configuration),
        }


@dataclass(frozen=True)
class Prediction:
    schema_version: str
    run_id: str
    case_id: str
    status: EvaluationStatus
    trigger: Mapping[str, Any]
    artifact: Mapping[str, Any]
    consumption: Mapping[str, Any]
    outcome: Mapping[str, Any]
    cost: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status.value,
            "trigger": dict(self.trigger),
            "artifact": dict(self.artifact),
            "consumption": dict(self.consumption),
            "outcome": dict(self.outcome),
            "cost": dict(self.cost),
        }


@dataclass(frozen=True)
class Report:
    schema_version: str
    run_id: str
    status: EvaluationStatus
    total: int
    passed: int
    failed: int
    dimensions: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "status": self.status.value,
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "dimensions": dict(self.dimensions),
        }
