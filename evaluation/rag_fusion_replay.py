"""Pure offline replay for a predeclared lexical/dense RRF grid."""

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
FINAL_K = 20
LEXICAL_SOURCE = "raw:lexical"
DENSE_SOURCE = "raw:vector"


@dataclass(frozen=True)
class FusionReplayConfig:
    config_id: str
    lexical_weight: float
    dense_weight: float
    rrf_k: int

    @property
    def weights(self) -> dict[str, float]:
        return {
            LEXICAL_SOURCE: self.lexical_weight,
            DENSE_SOURCE: self.dense_weight,
        }


def _config(lexical: float, dense: float, rrf_k: int) -> FusionReplayConfig:
    return FusionReplayConfig(
        config_id=(
            f"lexical-{round(lexical * 100):03d}-"
            f"dense-{round(dense * 100):03d}-rrf-{rrf_k:02d}"
        ),
        lexical_weight=lexical,
        dense_weight=dense,
        rrf_k=rrf_k,
    )


FUSION_CONFIGS = tuple(
    _config(lexical, dense, rrf_k)
    for lexical, dense in (
        (1.0, 0.0),
        (0.75, 0.25),
        (0.5, 0.5),
        (0.25, 0.75),
        (0.0, 1.0),
    )
    for rrf_k in (10, 30, 60)
)


def replay_fusion_grid(
    *,
    dataset: RagDataset,
    split: str,
    capture_rows: Sequence[Mapping[str, Any]],
    run_id: str,
) -> dict[str, Any]:
    """Replay every config from one persisted source-ranked capture."""
    if split != "dev":
        raise ValueError("fusion parameter selection is restricted to dev")
    cases = dataset.select_cases(split)
    if not cases:
        raise ValueError(f"dataset split has no cases: {split}")
    rows = _validate_capture(cases, capture_rows)
    status_counts: dict[str, int] = {}
    for row in rows.values():
        status = str(row["retrieval_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    system_failures = sum(
        count
        for status, count in status_counts.items()
        if status not in {RetrievalStatus.OK.value, RetrievalStatus.NO_EVIDENCE.value}
    )

    results = [_evaluate_config(config, cases, rows) for config in FUSION_CONFIGS]
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
    return {
        "schema_version": 1,
        "runner_version": "offline-rag-fusion-replay-v1",
        "run_id": run_id,
        "run_status": ("COMPLETED" if not system_failures else "COMPLETED_WITH_ERRORS"),
        "evaluation_role": "DEV_FUSION_SELECTION",
        "evaluation_scope": "knowledge_source_capture_offline_fusion_only",
        "split": split,
        "case_count": len(cases),
        "scored_case_count": sum(case.answerable for case in cases),
        "source_k": SOURCE_K,
        "candidate_k": FINAL_K,
        "query_mode": "raw_only",
        "selection_order": [
            "all_evidence_recall_at_20",
            "evidence_recall_at_20",
            "mrr_at_20",
            "ndcg_at_20",
            "config_id",
        ],
        "retrieval_status_counts": status_counts,
        "system_failure_count": system_failures,
        "configs": results,
        "selected_config": (
            None
            if selected is None
            else {
                key: selected[key]
                for key in (
                    "config_id",
                    "lexical_weight",
                    "dense_weight",
                    "rrf_k",
                )
            }
        ),
        "excluded_stages": [
            "standalone_query",
            "rerank",
            "parent_expansion",
            "packing",
            "generation",
            "judge",
        ],
    }


def _evaluate_config(config, cases, rows) -> dict[str, Any]:
    metrics = []
    for case in cases:
        row = rows[case.case_id]
        rankings = {
            source: tuple(map(str, row["source_rankings"].get(source, ())))
            for source in (LEXICAL_SOURCE, DENSE_SOURCE)
        }
        ranked_ids = fuse_rankings(
            rankings,
            weights=config.weights,
            rrf_k=config.rrf_k,
            top_k=FINAL_K,
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
                    top_k=FINAL_K,
                )
            )
    if not metrics:
        raise ValueError("fusion replay requires answerable cases")
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


def _validate_capture(cases, capture_rows):
    expected = {case.case_id for case in cases}
    rows: dict[str, Mapping[str, Any]] = {}
    for row in capture_rows:
        case_id = str(row.get("case_id") or "")
        if case_id in rows:
            raise ValueError(f"duplicate capture case ID: {case_id}")
        rows[case_id] = row
        rankings = row.get("source_rankings") or {}
        candidates = {str(item["chunk_id"]) for item in row.get("candidates") or ()}
        for source in (LEXICAL_SOURCE, DENSE_SOURCE):
            ranked = tuple(map(str, rankings.get(source, ())))
            if len(ranked) > SOURCE_K or len(ranked) != len(set(ranked)):
                raise ValueError(f"invalid {source} ranking for {case_id}")
            if not set(ranked).issubset(candidates):
                raise ValueError(f"{source} ranking missing canonical candidates")
    if set(rows) != expected:
        raise ValueError("capture case IDs do not match the selected dataset split")
    return rows
