"""Direct Understanding adapter runner with three stable JSON artifacts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from evaluation.command_primary_eval.contracts import (
    EvalCase,
    EvaluationStatus,
    Prediction,
    Report,
    RunManifest,
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
class UnderstandingDirectResult:
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


class UnderstandingDirectAdapter(Protocol):
    version: str

    async def evaluate(self, case: EvalCase) -> UnderstandingDirectResult: ...


class UnderstandingDirectRunner:
    """Run state-aware cases directly against an injected Understanding adapter."""

    schema_version = "command-primary-eval-v1"
    evaluator_version = "understanding-direct-runner-v1"

    def __init__(self, adapter: UnderstandingDirectAdapter) -> None:
        self._adapter = adapter

    async def run(
        self,
        cases: Sequence[EvalCase],
        output_dir: str | Path,
        *,
        run_id: str,
        dataset_id: str,
        split: str,
        configuration: Mapping[str, str] | None = None,
    ) -> Report:
        materialized = tuple(cases)
        destination = Path(output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        manifest = RunManifest(
            schema_version=self.schema_version,
            run_id=run_id,
            component="understanding",
            dataset_id=dataset_id,
            split=split,
            dataset_checksum=_dataset_checksum(materialized),
            evaluator_version=self.evaluator_version,
            adapter_version=self._adapter.version,
            case_count=len(materialized),
            created_at=datetime.now(timezone.utc).isoformat(),
            configuration=dict(configuration or {}),
        )
        _write_json(destination / "manifest.json", manifest.to_dict())

        predictions = []
        for case in materialized:
            result = await self._adapter.evaluate(case)
            predictions.append(Prediction(
                schema_version=self.schema_version,
                run_id=run_id,
                case_id=case.case_id,
                status=(
                    EvaluationStatus.PASS if result.passed else EvaluationStatus.FAIL
                ),
                trigger=result.trigger.to_dict(),
                artifact=result.artifact.to_dict(),
                consumption=result.consumption.to_dict(),
                outcome=result.outcome.to_dict(),
                cost=result.cost.to_dict(),
            ))
        _write_jsonl(
            destination / "predictions.jsonl",
            (prediction.to_dict() for prediction in predictions),
        )

        report = _report(run_id, predictions)
        _write_json(destination / "report.json", report.to_dict())
        return report


def _report(run_id: str, predictions: Sequence[Prediction]) -> Report:
    total = len(predictions)
    passed = sum(item.status is EvaluationStatus.PASS for item in predictions)

    def check(name: str) -> dict[str, Any]:
        numerator = sum(bool(getattr(item, name).get("passed")) for item in predictions)
        return {
            "numerator": numerator,
            "denominator": total,
            "rate": numerator / total if total else 0.0,
        }

    latencies = [float(item.cost["latency_ms"]) for item in predictions]
    return Report(
        schema_version=UnderstandingDirectRunner.schema_version,
        run_id=run_id,
        status=(
            EvaluationStatus.PASS
            if total and passed == total
            else EvaluationStatus.FAIL if total else EvaluationStatus.NOT_RUN
        ),
        total=total,
        passed=passed,
        failed=total - passed,
        dimensions={
            "trigger": check("trigger"),
            "artifact": check("artifact"),
            "consumption": check("consumption"),
            "outcome": check("outcome"),
            "cost": {
                "total_latency_ms": sum(latencies),
                "mean_latency_ms": sum(latencies) / total if total else 0.0,
                "total_token_usage": sum(
                    int(item.cost["token_usage"]) for item in predictions
                ),
                "total_invocations": sum(
                    int(item.cost["invocation_count"]) for item in predictions
                ),
            },
        },
    )


def _dataset_checksum(cases: Sequence[EvalCase]) -> str:
    payload = [case.to_dict() for case in cases]
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(_canonical_json(value) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Any) -> None:
    path.write_text(
        "".join(_canonical_json(value) + "\n" for value in values),
        encoding="utf-8",
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
