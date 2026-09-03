"""Pure offline Raw/Standalone replay over captured dense rankings."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from application.hybrid_retrieval import RetrievalStatus
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.rank_fusion import fuse_rankings


SOURCE_K = 40
CANDIDATE_K = 20
RRF_K = 10
RAW_SOURCE = "raw:vector"
STANDALONE_SOURCE = "standalone:vector"


@dataclass(frozen=True)
class QueryReplayConfig:
    config_id: str
    raw_weight: float
    standalone_weight: float

    @property
    def weights(self) -> dict[str, float]:
        return {
            RAW_SOURCE: self.raw_weight,
            STANDALONE_SOURCE: self.standalone_weight,
        }


def _config(raw: float, standalone: float) -> QueryReplayConfig:
    return QueryReplayConfig(
        config_id=(
            f"raw-{round(raw * 100):03d}-standalone-{round(standalone * 100):03d}"
        ),
        raw_weight=raw,
        standalone_weight=standalone,
    )


QUERY_CONFIGS = tuple(
    _config(raw, standalone)
    for raw, standalone in (
        (1.0, 0.0),
        (0.5, 0.5),
        (0.25, 0.75),
        (0.0, 1.0),
    )
)


def replay_query_grid(
    *,
    dataset: RagDataset,
    capture_rows: Sequence[Mapping[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    """Select query weights on the captured Dev cohort only."""
    cases, rows = _validate_capture(dataset, capture_rows)
    status_counts: dict[str, int] = {}
    for row in rows.values():
        status = str(row["retrieval_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    system_failures = sum(
        count
        for status, count in status_counts.items()
        if status
        not in {
            RetrievalStatus.OK.value,
            RetrievalStatus.NO_EVIDENCE.value,
        }
    )
    results = [_evaluate(config, cases, rows) for config in QUERY_CONFIGS]
    selected = (
        None
        if system_failures
        else min(
            results,
            key=lambda item: (
                -float(item["all_evidence_recall_at_20"]["rate"]),
                -float(item["evidence_recall_at_20"]),
                -float(item["mrr_at_20"]),
                -float(item["ndcg_at_20"]),
                str(item["config_id"]),
            ),
        )
    )
    rewrite_status_counts: dict[str, int] = {}
    for row in rows.values():
        status = str(row["rewrite_status"])
        rewrite_status_counts[status] = rewrite_status_counts.get(status, 0) + 1
    return {
        "schema_version": 1,
        "runner_version": "offline-rag-query-replay-v1",
        "run_id": run_id,
        "run_status": ("COMPLETED" if not system_failures else "COMPLETED_WITH_ERRORS"),
        "evaluation_role": "DEV_QUERY_SELECTION_REUSED_CAPTURE",
        "evaluation_scope": "knowledge_dense_query_replay_only",
        "split": "dev",
        "case_count": len(cases),
        "scored_case_count": sum(case.answerable for case in cases),
        "source_k": SOURCE_K,
        "candidate_k": CANDIDATE_K,
        "rrf_k": RRF_K,
        "retrieval_status_counts": status_counts,
        "rewrite_status_counts": rewrite_status_counts,
        "system_failure_count": system_failures,
        "selection_order": [
            "all_evidence_recall_at_20",
            "evidence_recall_at_20",
            "mrr_at_20",
            "ndcg_at_20",
            "config_id",
        ],
        "configs": results,
        "selected_config": (
            None
            if selected is None
            else {
                key: selected[key]
                for key in (
                    "config_id",
                    "raw_weight",
                    "standalone_weight",
                )
            }
        ),
        "excluded_stages": [
            "lexical_retrieval",
            "rerank",
            "parent_expansion",
            "packing",
            "generation",
            "judge",
        ],
    }


def _evaluate(config, cases, rows) -> dict[str, Any]:
    metrics = []
    for case in cases:
        row = rows[case.case_id]
        rankings = {
            source: tuple(map(str, row["source_rankings"].get(source, ())))
            for source in (RAW_SOURCE, STANDALONE_SOURCE)
        }
        weights = (
            config.weights
            if row["rewrite_status"] == "REWRITTEN"
            else {RAW_SOURCE: 1.0}
        )
        ranked_ids = fuse_rankings(
            rankings,
            weights=weights,
            rrf_k=RRF_K,
            top_k=CANDIDATE_K,
        )
        candidates = {
            str(item["chunk_id"]): {
                "document_id": str(item["document_id"]),
                "source_start_char": int(item["source_start_char"]),
                "source_end_char": int(item["source_end_char"]),
            }
            for item in row["candidates"]
        }
        if case.answerable:
            metrics.append(
                evaluate_ranked_hits(
                    case,
                    ranked_ids,
                    candidates,
                    top_k=CANDIDATE_K,
                )
            )
    if not metrics:
        raise ValueError("query replay requires answerable cases")
    all_evidence = sum(item["evidence_recall"] == 1.0 for item in metrics)
    return {
        **asdict(config),
        "all_evidence_recall_at_20": {
            "passed": all_evidence,
            "total": len(metrics),
            "rate": all_evidence / len(metrics),
        },
        "evidence_recall_at_20": statistics.fmean(
            item["evidence_recall"] for item in metrics
        ),
        "mrr_at_20": statistics.fmean(item["mrr"] for item in metrics),
        "ndcg_at_20": statistics.fmean(item["ndcg"] for item in metrics),
    }


def _validate_capture(dataset, capture_rows):
    dev_cases = {case.case_id: case for case in dataset.select_cases("dev")}
    rows: dict[str, Mapping[str, Any]] = {}
    for row in capture_rows:
        case_id = str(row.get("case_id") or "")
        if case_id in rows:
            raise ValueError(f"duplicate capture case ID: {case_id}")
        if case_id not in dev_cases:
            raise ValueError(f"capture case is not in dataset Dev: {case_id}")
        rows[case_id] = row
        rankings = row.get("source_rankings") or {}
        candidates = {str(item["chunk_id"]) for item in row.get("candidates") or ()}
        for source in (RAW_SOURCE, STANDALONE_SOURCE):
            ranked = tuple(map(str, rankings.get(source, ())))
            if len(ranked) > SOURCE_K or len(ranked) != len(set(ranked)):
                raise ValueError(f"invalid {source} ranking for {case_id}")
            if not set(ranked).issubset(candidates):
                raise ValueError(f"{source} ranking missing canonical candidates")
        if row.get("rewrite_status") not in {
            "REWRITTEN",
            "FALLBACK_IDENTICAL",
            "FALLBACK_ERROR",
        }:
            raise ValueError(f"invalid rewrite status for {case_id}")
    if not rows:
        raise ValueError("query capture is empty")
    cases = tuple(dev_cases[case_id] for case_id in sorted(rows))
    return cases, rows
