"""Checksum-bound artifacts for production-aligned fresh heldout runs."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from application.hybrid_retrieval import RetrievalGeneration, RetrievalStatus
from application.knowledge_retriever import KnowledgeRetrievalPolicy
from core.model_policy import ModelPolicy, ModelRole
from evaluation.postgres_rag_production_heldout_eval import RUNNER_VERSION
from evaluation.rag_heldout_projections import (
    embedding_profile_projection,
    generation_identity_projection,
)


def write_production_heldout_artifacts(
    *, output_dir: Path, dataset, predictions: Sequence[Mapping[str, Any]],
    run_id: str, generation: RetrievalGeneration,
    policy: KnowledgeRetrievalPolicy, model_policy: ModelPolicy,
    chunk_count: int, index_prepare_latency_ms: float,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("heldout output must not already exist")
    output_dir.mkdir(parents=True)
    predictions_path = output_dir / "predictions.jsonl"
    _write_jsonl(predictions_path, predictions)
    report = build_production_heldout_report(predictions, run_id=run_id)
    report_path = output_dir / "report.json"
    _write_json(report_path, report)
    manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "FRESH_HELDOUT_REPORT_ONLY",
        "configuration_selection_allowed": False,
        "configuration_fixed_before_run": True,
        "dataset": {
            "dataset_id": dataset.manifest["dataset_id"],
            "case_count": len(predictions),
            "document_count": len(dataset.documents),
            "corpus_sha256": dataset.manifest["corpus_sha256"],
            "cases_sha256": dataset.manifest["cases_sha256"],
            "source": dict(dataset.manifest["source"]),
        },
        "policy": {**policy.__dict__, "fingerprint": policy.fingerprint},
        "model_policy": {
            "provider": model_policy.provider,
            "base_url": model_policy.base_url or "official",
            "rewrite": model_policy.profile(ModelRole.REWRITE).to_dict(),
            "rerank": model_policy.profile(ModelRole.RERANK).to_dict(),
        },
        "embedding_profile": embedding_profile_projection(generation),
        "generation": generation_identity_projection(generation),
        "chunk_count": chunk_count,
        "index_prepare_latency_ms": index_prepare_latency_ms,
        "stages_executed": [
            "contextual_child_embedding", "raw_query", "standalone_query",
            "bounded_query_expansion", "dense_top20", "bm25_top20",
            "weighted_rrf_candidate_top20", "listwise_llm_rerank",
            "top5_pack_2600", "evidence_pack",
        ],
        "stages_not_run": ["hyde", "parent_window_expansion", "generation", "judge"],
        "artifacts": {
            "predictions.jsonl": _file_sha256(predictions_path),
            "report.json": _file_sha256(report_path),
        },
    }
    _write_json(output_dir / "manifest.json", manifest)
    return report


def build_production_heldout_report(
    predictions: Sequence[Mapping[str, Any]], *, run_id: str,
) -> dict[str, Any]:
    if not predictions:
        raise ValueError("heldout predictions are required")
    statuses = _counts(predictions, "retrieval_status")
    failures = sum(
        count for status, count in statuses.items()
        if status not in {RetrievalStatus.OK.value, RetrievalStatus.NO_EVIDENCE.value}
    )
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_status": "COMPLETED" if not failures else "COMPLETED_WITH_ERRORS",
        "evaluation_role": "FRESH_HELDOUT_REPORT_ONLY",
        "selection_performed": False,
        "case_count": len(predictions),
        "candidate": _metrics(predictions, "candidate_metrics"),
        "prepack": _metrics(predictions, "prepack_metrics"),
        "packed": _metrics(predictions, "packed_metrics"),
        "candidate_to_prepack_loss_count": sum(
            item["candidate_metrics"]["evidence_recall"] == 1.0
            and item["prepack_metrics"]["evidence_recall"] < 1.0
            for item in predictions
        ),
        "packing_harmful_count": sum(
            item["packing_change"] == "HARMFUL" for item in predictions
        ),
        "rerank_fallback_count": sum(
            bool(item["rerank_fallback"]) for item in predictions
        ),
        "retrieval_status_counts": statuses,
        "system_failure_count": failures,
        "mean_tokens": statistics.fmean(
            int(item["token_count"]) for item in predictions
        ),
        "end_to_end_latency_ms": _latency([
            float(item["end_to_end_latency_ms"]) for item in predictions
        ]),
        "llm_usage": _llm_usage(predictions),
        "by_domain": _slices(predictions, "domain"),
        "by_document_length": _slices(predictions, "document_length"),
    }


def build_combined_report(
    reports: Sequence[Mapping[str, Any]], *, run_id: str,
) -> dict[str, Any]:
    if not reports:
        raise ValueError("combined report requires component reports")
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "evaluation_role": "FRESH_HELDOUT_COMBINED_PROJECTION",
        "selection_performed": False,
        "case_count": sum(int(item["case_count"]) for item in reports),
        "components": list(reports),
    }


def write_combined_production_heldout_artifacts(
    *, output_dir: Path, component_dirs: Sequence[Path], run_id: str,
) -> dict[str, Any]:
    """Verify immutable component artifacts and project aggregate metrics.

    The component predictions remain the authoritative row-level evidence.  The
    combined directory contains only a checksum-bound projection so it cannot
    silently diverge from either completed heldout run.
    """
    if output_dir.exists():
        raise ValueError("combined heldout output must not already exist")
    if len(component_dirs) < 2:
        raise ValueError("combined heldout report requires at least two components")

    components: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    seen_group_ids: set[str] = set()
    invariant: tuple[Any, ...] | None = None
    for component_dir in component_dirs:
        manifest_path = component_dir / "manifest.json"
        report_path = component_dir / "report.json"
        predictions_path = component_dir / "predictions.jsonl"
        manifest = _read_json(manifest_path)
        component_report = _read_json(report_path)
        expected_artifacts = manifest.get("artifacts")
        if not isinstance(expected_artifacts, Mapping):
            raise ValueError(f"missing artifact checksums: {component_dir}")
        for name, path in (
            ("predictions.jsonl", predictions_path), ("report.json", report_path),
        ):
            if expected_artifacts.get(name) != _file_sha256(path):
                raise ValueError(f"component artifact checksum mismatch: {path}")
        component_run_id = str(manifest.get("run_id") or "")
        if component_run_id != str(component_report.get("run_id") or ""):
            raise ValueError(f"component run_id mismatch: {component_dir}")
        if component_report.get("run_status") != "COMPLETED":
            raise ValueError(f"component run is not complete: {component_dir}")
        if int(component_report.get("system_failure_count", -1)) != 0:
            raise ValueError(f"component contains system failures: {component_dir}")

        rows = _read_jsonl(predictions_path)
        expected_count = int(manifest["dataset"]["case_count"])
        if len(rows) != expected_count or int(component_report["case_count"]) != expected_count:
            raise ValueError(f"component case count mismatch: {component_dir}")
        if any(str(row.get("run_id") or "") != component_run_id for row in rows):
            raise ValueError(f"prediction run_id mismatch: {component_dir}")
        case_ids = {str(row["case_id"]) for row in rows}
        group_ids = {str(row["group_id"]) for row in rows}
        if len(case_ids) != len(rows) or seen_case_ids.intersection(case_ids):
            raise ValueError("combined heldout case IDs must be unique")
        if len(group_ids) != len(rows) or seen_group_ids.intersection(group_ids):
            raise ValueError("combined heldout group IDs must be unique")
        seen_case_ids.update(case_ids)
        seen_group_ids.update(group_ids)

        component_invariant = (
            manifest["dataset"]["corpus_sha256"],
            manifest["policy"]["fingerprint"],
            manifest["embedding_profile"]["fingerprint"],
            manifest["generation"]["immutable_fingerprint"],
            json.dumps(manifest["model_policy"], sort_keys=True),
            tuple(manifest["stages_executed"]),
            tuple(manifest["stages_not_run"]),
        )
        if invariant is None:
            invariant = component_invariant
        elif component_invariant != invariant:
            raise ValueError("combined heldout components used different frozen configurations")
        predictions.extend(rows)
        components.append({
            "path": str(component_dir),
            "run_id": component_run_id,
            "dataset_id": manifest["dataset"]["dataset_id"],
            "case_count": expected_count,
            "cases_sha256": manifest["dataset"]["cases_sha256"],
            "predictions_sha256": expected_artifacts["predictions.jsonl"],
            "report_sha256": expected_artifacts["report.json"],
        })

    report = build_production_heldout_report(predictions, run_id=run_id)
    report["evaluation_role"] = "FRESH_HELDOUT_COMBINED_PROJECTION"
    report["components"] = components
    output_dir.mkdir(parents=True)
    report_path = output_dir / "report.json"
    _write_json(report_path, report)
    manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "FRESH_HELDOUT_COMBINED_PROJECTION",
        "configuration_selection_allowed": False,
        "case_count": len(predictions),
        "case_ids_sha256": hashlib.sha256(
            "\n".join(sorted(seen_case_ids)).encode("utf-8")
        ).hexdigest(),
        "components": components,
        "frozen_configuration": {
            "corpus_sha256": invariant[0],
            "policy_fingerprint": invariant[1],
            "embedding_fingerprint": invariant[2],
            "generation_immutable_fingerprint": invariant[3],
        },
        "artifacts": {"report.json": _file_sha256(report_path)},
    }
    _write_json(output_dir / "manifest.json", manifest)
    return report


def _metrics(rows, field):
    values = [item[field] for item in rows]
    complete = sum(float(item["evidence_recall"]) == 1.0 for item in values)
    return {
        "all_evidence_recall": {
            "passed": complete, "total": len(values),
            "rate": complete / len(values),
        },
        **{
            key: statistics.fmean(float(item[key]) for item in values)
            for key in ("evidence_recall", "document_recall", "mrr", "ndcg")
        },
    }


def _slices(rows, field):
    return {
        value: {
            "case_count": len(selected),
            "candidate": _metrics(selected, "candidate_metrics"),
            "prepack": _metrics(selected, "prepack_metrics"),
            "packed": _metrics(selected, "packed_metrics"),
        }
        for value in sorted({str(item[field]) for item in rows})
        if (selected := [item for item in rows if str(item[field]) == value])
    }


def _llm_usage(rows):
    fields = ("calls", "errors", "input_tokens", "output_tokens")
    return {
        field: sum(int(item["llm_usage"]["total"][field]) for item in rows)
        for field in fields
    }


def _latency(values):
    ordered = sorted(values)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    return {"mean": statistics.fmean(values), "p95": p95, "max": max(values)}


def _counts(rows, field):
    result: dict[str, int] = {}
    for item in rows:
        value = str(item[field])
        result[value] = result.get(value, 0) + 1
    return result


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"expected JSON objects in: {path}")
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text("".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
        for row in rows
    ), encoding="utf-8")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
