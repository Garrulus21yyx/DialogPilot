"""Build the standard artifacts for the fixed Knowledge heldout run."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from application.hybrid_retrieval import RetrievalGeneration, RetrievalStatus
from application.knowledge_retriever import KnowledgeRetrievalPolicy
from core.model_policy import ModelPolicy, ModelRole
from evaluation.postgres_rag_candidate_eval import CHUNK_PROFILES
from evaluation.postgres_rag_heldout_eval import (
    CANDIDATE_K,
    CHUNK_PROFILE_ID,
    CONTEXT_MAX_TOKENS,
    FINAL_K,
    RUNNER_VERSION,
    SOURCE_K,
)
from evaluation.rag_heldout_projections import (
    embedding_profile_projection,
    generation_identity_projection,
)
from evaluation.rag_pipeline.dataset import RagDataset
from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION


def write_heldout_artifacts(
    *,
    output_dir: Path,
    dataset: RagDataset,
    predictions: Sequence[Mapping[str, Any]],
    run_id: str,
    generation: RetrievalGeneration,
    policy: KnowledgeRetrievalPolicy,
    model_policy: ModelPolicy,
    chunk_count: int,
    index_prepare_latency_ms: float,
) -> dict[str, Any]:
    if output_dir.exists():
        raise ValueError("heldout output must not already exist")
    output_dir.mkdir(parents=True)
    predictions_path = output_dir / "predictions.jsonl"
    _write_jsonl(predictions_path, predictions)
    report = build_heldout_report(predictions, run_id=run_id)
    report_path = output_dir / "report.json"
    _write_json(report_path, report)
    manifest = _build_manifest(
        dataset=dataset,
        predictions=predictions,
        run_id=run_id,
        generation=generation,
        policy=policy,
        model_policy=model_policy,
        chunk_count=chunk_count,
        index_prepare_latency_ms=index_prepare_latency_ms,
        predictions_sha256=_file_sha256(predictions_path),
        report_sha256=_file_sha256(report_path),
        report=report,
    )
    _write_json(output_dir / "manifest.json", manifest)
    return report


def build_heldout_report(
    predictions: Sequence[Mapping[str, Any]], *, run_id: str
) -> dict[str, Any]:
    if not predictions:
        raise ValueError("heldout predictions are required")
    status_counts = _counts(predictions, "retrieval_status")
    system_failures = sum(
        count
        for status, count in status_counts.items()
        if status not in {RetrievalStatus.OK.value, RetrievalStatus.NO_EVIDENCE.value}
    )
    rewrite_counts = _counts((item["rewrite"] for item in predictions), "status")
    tokens = [int(item["token_count"]) for item in predictions]
    retrieval_latencies = [float(item["retrieval_latency_ms"]) for item in predictions]
    packing_latencies = [float(item["packing_cpu_latency_ms"]) for item in predictions]
    rewrite_latencies = [
        float(item["rewrite"]["usage"]["latency_ms"]["sum"]) for item in predictions
    ]
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_status": "COMPLETED" if not system_failures else "COMPLETED_WITH_ERRORS",
        "evaluation_role": "HELDOUT_FIXED_BASELINE",
        "selection_performed": False,
        "case_count": len(predictions),
        "fixed_config_id": "fixed512-dense-standalone-fallback-off-top5-budget2600",
        "candidate": _metrics(predictions, "candidate_metrics"),
        "prepack": _metrics(predictions, "prepack_metrics"),
        "packed": _metrics(predictions, "packed_metrics"),
        "packing_helpful_count": sum(
            item["packing_change"] == "HELPFUL" for item in predictions
        ),
        "packing_harmful_count": sum(
            item["packing_change"] == "HARMFUL" for item in predictions
        ),
        "mean_tokens": statistics.fmean(tokens),
        "p95_tokens": _p95(tokens),
        "rewrite": {
            "status_counts": rewrite_counts,
            "usage": _usage(predictions),
            "per_case_latency_ms": _latency(rewrite_latencies),
        },
        "retrieval_status_counts": status_counts,
        "system_failure_count": system_failures,
        "retrieval_latency_ms": _latency(retrieval_latencies),
        "packing_cpu_latency_ms": _latency(packing_latencies),
        "by_document_length": _slices(predictions, "document_length"),
        "by_domain": _slices(predictions, "domain"),
        "excluded_stages": [
            "lexical_retrieval",
            "query_expansion",
            "hyde",
            "cross_encoder_rerank",
            "llm_rerank",
            "parent_expansion",
            "generation",
            "judge",
        ],
    }


def _build_manifest(
    *,
    dataset,
    predictions,
    run_id,
    generation,
    policy,
    model_policy,
    chunk_count,
    index_prepare_latency_ms,
    predictions_sha256,
    report_sha256,
    report,
):
    profile = CHUNK_PROFILES[CHUNK_PROFILE_ID]
    rewrite = model_policy.profile(ModelRole.REWRITE)
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "HELDOUT_FIXED_BASELINE",
        "configuration_selection_allowed": False,
        "configuration_fixed_before_run": True,
        "dataset": {
            "dataset_id": dataset.manifest["dataset_id"],
            "split": "heldout",
            "case_count": len(predictions),
            "conversation_count": len({item["group_id"] for item in predictions}),
            "document_count": len(dataset.documents),
            "corpus_sha256": dataset.manifest["corpus_sha256"],
            "cases_sha256": dataset.manifest["cases_sha256"],
            "source": dict(dataset.manifest["source"]),
        },
        "query_transform": {
            "owner": "mcp.query_transformer.QueryTransformer.standalone",
            "prompt_version": QUERY_TRANSFORM_PROMPT_VERSION,
            "calls_per_case_max": 1,
            "fallback": "error/empty/identical -> raw",
            "provider": model_policy.provider,
            "base_url": model_policy.base_url or "official",
            "profile": rewrite.to_dict(),
            "actual_usage": report["rewrite"]["usage"],
        },
        "fixed_config": {
            "chunk_profile": {"profile_id": CHUNK_PROFILE_ID, **asdict(profile)},
            "source_k": SOURCE_K,
            "candidate_k": CANDIDATE_K,
            "dense_weight": 1.0,
            "lexical_weight": 0.0,
            "raw_weight": 0.0,
            "standalone_weight": 1.0,
            "production_fallback_to_raw": True,
            "reranker": "identity-no-rerank-v1",
            "final_k": FINAL_K,
            "context_max_tokens": CONTEXT_MAX_TOKENS,
            "packer": "context-packer-v1",
        },
        "capture_policy": {**asdict(policy), "fingerprint": policy.fingerprint},
        "embedding_profile": embedding_profile_projection(generation),
        "generation": generation_identity_projection(generation),
        "chunk_count": chunk_count,
        "index_prepare_latency_ms": index_prepare_latency_ms,
        "stages_executed": [
            "live_standalone_rewrite",
            "production_raw_fallback",
            "dense_source_top_40",
            "candidate_top_20",
            "identity_top_5",
            "context_pack_2600",
            "evidence_pack",
        ],
        "stages_not_run": report["excluded_stages"],
        "artifacts": {
            "predictions.jsonl": predictions_sha256,
            "report.json": report_sha256,
        },
    }


def _metrics(rows, field):
    values = [item[field] for item in rows]
    all_evidence = sum(float(item["evidence_recall"]) == 1.0 for item in values)
    return {
        "all_evidence_recall": {
            "passed": all_evidence,
            "total": len(values),
            "rate": all_evidence / len(values),
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
            "packed": _metrics(selected, "packed_metrics"),
        }
        for value in sorted({str(item[field]) for item in rows})
        if (selected := [item for item in rows if str(item[field]) == value])
    }


def _usage(rows):
    fields = (
        "calls",
        "errors",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    return {
        field: sum(int(item["rewrite"]["usage"].get(field, 0)) for item in rows)
        for field in fields
    }


def _latency(values):
    return {"mean": statistics.fmean(values), "p95": _p95(values), "max": max(values)}


def _p95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _counts(rows, field):
    counts: dict[str, int] = {}
    for item in rows:
        value = str(item[field])
        counts[value] = counts.get(value, 0) + 1
    return counts


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        )
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
