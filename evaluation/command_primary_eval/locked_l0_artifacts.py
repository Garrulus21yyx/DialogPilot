"""Artifact writer for the locked L0 live clarification evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from application.default_flow_registry import command_primary_flow_registry
from application.structured_command_prompt import COMMAND_ROUTER_PROMPT_VERSION
from evaluation.command_primary_eval.direct_runner import write_json, write_jsonl
from evaluation.command_primary_eval.locked_l0_clarification import (
    LiveProviderIdentity,
    load_locked_l0_clarification_cases,
)


EVALUATOR_VERSION = "locked-l0-clarification-eval-v1"
EVALUATION_SCOPE = "LOCKED_L0_CLARIFICATION_COMMAND_CHAIN"
EVALUATION_ROLE = "DEV_CONTRACT_DIAGNOSTIC"


def write_locked_l0_run(
    *,
    dataset_root: str | Path,
    output_dir: str | Path,
    run_id: str,
    cases: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    provider: LiveProviderIdentity,
    completion_version: str,
    prompt_fingerprints: Sequence[str],
    registry: Any,
    reason_code: str = "",
) -> Mapping[str, Any]:
    eligible = bool(predictions) and all(row["score_eligible"] for row in predictions)
    status = (
        "COMPLETED"
        if eligible
        else "COMPLETED_WITH_SYSTEM_ERRORS"
        if provider.real_provider
        else "NOT_RUN"
    )
    passed = sum(row["status"] == "PASS" for row in predictions)
    latencies = [float(row["latency_ms"]) for row in predictions]
    check_names = sorted({name for row in predictions for name in row["checks"]})
    understanding_statuses = sorted(
        {str(row["actual"]["understanding_status"]) for row in predictions}
    )
    report = {
        "schema_version": 1,
        "evaluator_version": EVALUATOR_VERSION,
        "run_id": run_id,
        "run_status": status,
        "evaluation_role": EVALUATION_ROLE,
        "evaluation_scope": EVALUATION_SCOPE,
        "score_eligible": eligible,
        "case_count": len(cases),
        "passed": passed,
        "failed": len(predictions) - passed,
        "pass_rate": passed / len(predictions) if eligible else None,
        "provider_calls": sum(row["usage"]["calls"] for row in predictions),
        "provider_errors": sum(row["usage"]["errors"] for row in predictions),
        "check_pass_counts": {
            name: sum(bool(row["checks"].get(name)) for row in predictions)
            for name in check_names
        },
        "understanding_status_counts": {
            name: sum(
                str(row["actual"]["understanding_status"]) == name
                for row in predictions
            )
            for name in understanding_statuses
        },
        "cost": {
            "input_tokens": sum(row["usage"]["input_tokens"] for row in predictions),
            "output_tokens": sum(row["usage"]["output_tokens"] for row in predictions),
            "latency_ms": {
                "mean": statistics.fmean(latencies) if latencies else None,
                "p95": _nearest_rank(latencies, 0.95) if latencies else None,
                "percentile_method": "nearest_rank",
            },
        },
        "reason_code": reason_code,
    }
    manifest = {
        "schema_version": 1,
        "evaluator_version": EVALUATOR_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_status": status,
        "evaluation_role": EVALUATION_ROLE,
        "evaluation_scope": EVALUATION_SCOPE,
        "score_eligible": eligible,
        "reason_code": reason_code,
        "dataset": _dataset_identity(dataset_root, cases),
        "runtime": {
            "entrypoint": "ChatApplication.handle",
            "mode": "structured_knowledge_primary+locked_clarify_evaluation",
            "encoder": "always-defer-command-encoder-v1",
            "flow_registry_generation": registry.generation,
            "flow_registry_fingerprint": registry.fingerprint,
        },
        "provider": {
            "provider": provider.provider,
            "model": provider.model,
            "model_profile_fingerprint": provider.model_profile_fingerprint,
            "sdk_version": provider.sdk_version,
            "completion_transport_version": completion_version,
            "real_provider": provider.real_provider,
            "call_count": report["provider_calls"],
            "error_count": report["provider_errors"],
        },
        "prompt": {
            "version": COMMAND_ROUTER_PROMPT_VERSION,
            "system_prompt_sha256": sorted(set(prompt_fingerprints)),
        },
        "scoring": {
            "oracle": "locked deterministic assertions and turn expected fields",
            "unscored": ["expected_claims", "human_rubric"],
        },
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "manifest.json", manifest)
    write_jsonl(output / "predictions.jsonl", predictions)
    write_json(output / "report.json", report)
    return report


def write_locked_l0_not_run(
    *,
    dataset_root: str | Path,
    output_dir: str | Path,
    run_id: str,
    reason_code: str,
) -> Mapping[str, Any]:
    cases = load_locked_l0_clarification_cases(dataset_root)
    registry = command_primary_flow_registry(
        str(cases[0]["initial_state"]["tenant_id"])
    )
    return write_locked_l0_run(
        dataset_root=dataset_root,
        output_dir=output_dir,
        run_id=run_id,
        cases=cases,
        predictions=(),
        provider=LiveProviderIdentity("", "", "", "", False),
        completion_version="not-configured",
        prompt_fingerprints=(),
        registry=registry,
        reason_code=reason_code,
    )


def _dataset_identity(
    dataset_root: str | Path,
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    path = Path(dataset_root) / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return {
        "dataset_id": manifest["dataset_id"],
        "lock_status": manifest["lock_status"],
        "manifest_sha256": _sha256(path.read_bytes()),
        "cases_sha256": manifest["cases_sha256"],
        "selected_case_ids_sha256": _sha256(
            "\n".join(str(row["case_id"]) for row in cases).encode()
        ),
        "selected_case_count": len(cases),
    }


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
