"""Run and score the evaluator-owned LoCoMo session retrieval slice."""

from __future__ import annotations

import math
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.command_primary_eval.direct_runner import write_json, write_jsonl
from evaluation.command_primary_eval.locomo_session_contracts import (
    BenchmarkSessionDocument,
    LocomoSessionSlice,
    RankedSessionHit,
    SessionCandidateRetriever,
)


TOP_K = 5
RUNNER_VERSION = "locomo-session-retrieval-eval-v1"
CORPUS_SEMANTICS = "BENCHMARK_CONVERSATION_SESSION"
PRODUCTION_SEMANTICS = "NOT_EVALUATED"


class TokenOverlapSessionRetriever:
    """Small deterministic CLI baseline; stronger retrievers use the same port."""

    version = "token-overlap-session-baseline-v1"
    _token = re.compile(r"[a-z0-9]+")

    def retrieve(
        self,
        *,
        query: str,
        documents: Sequence[BenchmarkSessionDocument],
        top_k: int,
    ) -> Sequence[RankedSessionHit]:
        query_terms = Counter(self._token.findall(query.casefold()))
        ranked = []
        for document in documents:
            terms = Counter(self._token.findall(document.content.casefold()))
            score = float(
                sum(
                    min(count, terms.get(term, 0))
                    for term, count in query_terms.items()
                )
            )
            ranked.append(RankedSessionHit(document.session_id, score))
        return tuple(
            sorted(
                ranked,
                key=lambda item: (-item.score, _session_number(item.session_id)),
            )[:top_k]
        )


def evaluate_locomo_session_slice(
    *,
    dataset: LocomoSessionSlice,
    retriever: SessionCandidateRetriever,
    output_dir: str | Path,
    run_id: str,
    clock: Callable[[], float] = time.perf_counter,
) -> Mapping[str, Any]:
    predictions = []
    known_sessions = {item.session_id for item in dataset.documents}
    for case in dataset.cases:
        started = clock()
        hits = tuple(
            retriever.retrieve(
                query=case.question,
                documents=dataset.documents,
                top_k=TOP_K,
            )
        )
        latency_ms = (clock() - started) * 1000
        _validate_hits(hits, known_sessions)
        ranked_ids = tuple(item.session_id for item in hits)
        recalled = set(ranked_ids).intersection(case.gold_session_ids)
        recall_all = float(len(recalled) == len(set(case.gold_session_ids)))
        first_rank = next(
            (
                rank
                for rank, session_id in enumerate(ranked_ids, start=1)
                if session_id in case.gold_session_ids
            ),
            None,
        )
        predictions.append(
            {
                "schema_version": 1,
                "run_id": run_id,
                "question_id": case.question_id,
                "sample_id": case.conversation_id,
                "category": case.category,
                "gold_session_ids": list(case.gold_session_ids),
                "ranked_sessions": [
                    {
                        "rank": rank,
                        "session_id": hit.session_id,
                        "score": hit.score,
                    }
                    for rank, hit in enumerate(hits, start=1)
                ],
                "metrics": {
                    "recall_all_at_5": recall_all,
                    "mrr": 1.0 / first_rank if first_rank is not None else 0.0,
                },
                "latency_ms": latency_ms,
            }
        )

    report = _report(run_id, predictions)
    manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "component": "public_memory_session_retrieval",
        "corpus_semantics": CORPUS_SEMANTICS,
        "production_service_episode_semantics": PRODUCTION_SEMANTICS,
        "dataset": {
            "dataset_id": dataset.dataset_id,
            "source_revision": dataset.source_revision,
            "source_sha256": dataset.source_sha256,
            "sample_id": dataset.sample_id,
            "category": dataset.category,
            "question_type": "single-hop",
            "document_unit": "session",
            "document_count": len(dataset.documents),
            "case_count": len(dataset.cases),
        },
        "candidate_retriever": {
            "version": retriever.version,
            "top_k": TOP_K,
        },
        "stages_executed": ["session_adaptation", "candidate_retrieval"],
        "stages_not_run": [
            "service_episode_projection",
            "commitment_resolution",
            "preference_resolution",
            "answer_generation",
            "answer_judge",
        ],
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "manifest.json", manifest)
    write_jsonl(output / "predictions.jsonl", predictions)
    write_json(output / "report.json", report)
    return report


def _report(run_id: str, predictions: list[dict[str, Any]]) -> dict[str, Any]:
    recalls = [float(item["metrics"]["recall_all_at_5"]) for item in predictions]
    reciprocal_ranks = [float(item["metrics"]["mrr"]) for item in predictions]
    latencies = [float(item["latency_ms"]) for item in predictions]
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_status": "COMPLETED" if predictions else "NOT_RUN",
        "evaluation_scope": "benchmark_conversation_session_candidate_retrieval",
        "corpus_semantics": CORPUS_SEMANTICS,
        "production_service_episode_semantics": PRODUCTION_SEMANTICS,
        "case_count": len(predictions),
        "metrics": {
            "recall_all_at_5": statistics.fmean(recalls) if recalls else None,
            "mrr": statistics.fmean(reciprocal_ranks) if reciprocal_ranks else None,
        },
        "retrieval_latency_ms": {
            "p95": _nearest_rank_percentile(latencies, 0.95),
            "percentile_method": "nearest_rank",
        },
    }


def _validate_hits(
    hits: tuple[RankedSessionHit, ...],
    known_sessions: set[str],
) -> None:
    if len(hits) > TOP_K:
        raise ValueError("candidate retriever returned more than top_k")
    ids = tuple(item.session_id for item in hits)
    if len(ids) != len(set(ids)) or not set(ids).issubset(known_sessions):
        raise ValueError("candidate retriever returned invalid session IDs")


def _nearest_rank_percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _session_number(session_id: str) -> int:
    return int(session_id.removeprefix("S"))
