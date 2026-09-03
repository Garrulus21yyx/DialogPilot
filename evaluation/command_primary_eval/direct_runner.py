"""Shared execution and persistence for thin direct component evaluations."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from evaluation.command_primary_eval.contracts import (
    DirectEvaluationResult,
    EvalCase,
    EvaluationStatus,
    Prediction,
    Report,
    RunManifest,
)


class DirectEvaluator(Protocol):
    version: str

    async def evaluate(self, case: EvalCase) -> DirectEvaluationResult: ...


ExtraDimensions = Callable[[Sequence[Prediction]], Mapping[str, Any]]


class DirectRunner:
    """Run one injected component adapter and write the three shared artifacts."""

    schema_version = "command-primary-eval-v1"

    def __init__(
        self,
        adapter: DirectEvaluator,
        *,
        component: str,
        evaluator_version: str,
        default_configuration: Mapping[str, str] | None = None,
        extra_dimensions: ExtraDimensions | None = None,
    ) -> None:
        self._adapter = adapter
        self._component = component
        self._evaluator_version = evaluator_version
        self._default_configuration = dict(default_configuration or {})
        self._extra_dimensions = extra_dimensions

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
            component=self._component,
            dataset_id=dataset_id,
            split=split,
            dataset_checksum=dataset_checksum(materialized),
            evaluator_version=self._evaluator_version,
            adapter_version=self._adapter.version,
            case_count=len(materialized),
            created_at=datetime.now(timezone.utc).isoformat(),
            configuration={
                **self._default_configuration,
                **dict(configuration or {}),
            },
        )
        write_json(destination / "manifest.json", manifest.to_dict())

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
        write_jsonl(
            destination / "predictions.jsonl",
            (prediction.to_dict() for prediction in predictions),
        )
        report = build_report(
            run_id,
            predictions,
            extra_dimensions=(
                self._extra_dimensions(predictions)
                if self._extra_dimensions is not None else {}
            ),
        )
        write_json(destination / "report.json", report.to_dict())
        return report


def build_report(
    run_id: str,
    predictions: Sequence[Prediction],
    *,
    extra_dimensions: Mapping[str, Any] | None = None,
) -> Report:
    total = len(predictions)
    passed = sum(item.status is EvaluationStatus.PASS for item in predictions)
    latencies = [float(item.cost["latency_ms"]) for item in predictions]
    dimensions = {
        name: _rate(predictions, name)
        for name in ("trigger", "artifact", "consumption", "outcome")
    }
    dimensions.update(dict(extra_dimensions or {}))
    dimensions["cost"] = {
        "total_latency_ms": sum(latencies),
        "mean_latency_ms": sum(latencies) / total if total else 0.0,
        "total_token_usage": sum(
            int(item.cost["token_usage"]) for item in predictions
        ),
        "total_invocations": sum(
            int(item.cost["invocation_count"]) for item in predictions
        ),
    }
    return Report(
        schema_version=DirectRunner.schema_version,
        run_id=run_id,
        status=(
            EvaluationStatus.PASS
            if total and passed == total
            else EvaluationStatus.FAIL if total else EvaluationStatus.NOT_RUN
        ),
        total=total,
        passed=passed,
        failed=total - passed,
        dimensions=dimensions,
    )


def dataset_checksum(cases: Sequence[EvalCase]) -> str:
    payload = [case.to_dict() for case in cases]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def write_jsonl(path: Path, values: Any) -> None:
    path.write_text(
        "".join(canonical_json(value) + "\n" for value in values),
        encoding="utf-8",
    )


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rate(
    predictions: Sequence[Prediction],
    name: str,
) -> dict[str, int | float]:
    total = len(predictions)
    numerator = sum(bool(getattr(item, name).get("passed")) for item in predictions)
    return {
        "numerator": numerator,
        "denominator": total,
        "rate": numerator / total if total else 0.0,
    }
