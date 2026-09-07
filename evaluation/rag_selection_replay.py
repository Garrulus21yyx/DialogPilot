"""Replay no-rerank selection and production packing over a frozen Query run."""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from evaluation import rag_query_replay as query_replay
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.context_packer import CONTEXT_PACKER_VERSION, ContextCandidate, ContextPacker
from mcp.evidence_pack import EvidencePack
from mcp.rank_fusion import fuse_rankings
from mcp.source_document import SourceDocument


@dataclass(frozen=True)
class SelectionConfig:
    config_id: str
    final_k: int
    context_max_tokens: int


SELECTION_CONFIGS = tuple(
    SelectionConfig(f"top-{final_k:02d}-budget-{budget}", final_k, budget)
    for final_k in (3, 5, 8)
    for budget in (1800, 2600)
)


def replay_selection_grid(
    *,
    dataset: RagDataset,
    query_rows: Sequence[Mapping[str, Any]],
    manifest_fingerprint: str,
    run_id: str,
):
    """Replay a checksum-validated viewed Query run over the bounded grid."""
    cases = {case.case_id: case for case in dataset.select_cases("dev")}
    documents = {item.document_id: item for item in dataset.documents}
    packer = ContextPacker()
    predictions = []
    for row in sorted(query_rows, key=lambda item: str(item["case_id"])):
        case = cases[str(row["case_id"])]
        ranked_ids, route = _ranked_candidate_ids(row)
        candidates = _restore_candidates(
            row,
            ranked_ids,
            route,
            documents,
            manifest_fingerprint,
        )
        hits = _hits(candidates)
        candidate_metrics = _score(case, ranked_ids, hits, query_replay.CANDIDATE_K)
        configurations = []
        for config in SELECTION_CONFIGS:
            prepack_ids = ranked_ids[: config.final_k]
            prepack_metrics = _score(case, prepack_ids, hits, config.final_k)
            started = time.perf_counter()
            packed = packer.pack(
                candidates,
                max_tokens=config.context_max_tokens,
                max_chunks=config.final_k,
                redundancy_threshold=1.0,
            )
            packing_latency_ms = (time.perf_counter() - started) * 1000
            evidence_pack = EvidencePack.from_packed(
                case.query,
                packed,
                retrieval_policy={
                    "policy_version": "knowledge-selection-replay-v1",
                    "candidate_k": query_replay.CANDIDATE_K,
                    "top_k": config.final_k,
                    "context_max_tokens": config.context_max_tokens,
                    "reranker_version": "identity-no-rerank-v1",
                    "packer_version": CONTEXT_PACKER_VERSION,
                },
                retrieval_trace={"rerank_prompt_version": "identity-no-rerank-v1"},
            )
            packed_ids = tuple(item.chunk_id for item in evidence_pack.items)
            packed_metrics = _score(case, packed_ids, hits, config.final_k)
            change = (
                packed_metrics["evidence_recall"] - prepack_metrics["evidence_recall"]
            )
            configurations.append(
                {
                    **asdict(config),
                    "prepack_ids": list(prepack_ids),
                    "prepack_metrics": prepack_metrics,
                    "evidence_pack": evidence_pack.to_dict(include_text=False),
                    "packed_metrics": packed_metrics,
                    "packing_change": (
                        "HELPFUL"
                        if change > 0
                        else "HARMFUL"
                        if change < 0
                        else "NEUTRAL"
                    ),
                    "token_count": packed.token_count,
                    "packing_cpu_latency_ms": packing_latency_ms,
                }
            )
        predictions.append(
            {
                "schema_version": 1,
                "run_id": run_id,
                "case_id": case.case_id,
                "status": "OK",
                "rewrite_status": str(row["rewrite_status"]),
                "selected_query_route": route,
                "candidate_ids": list(ranked_ids),
                "candidate_metrics": candidate_metrics,
                "configurations": configurations,
            }
        )
    report = _build_report(run_id, predictions)
    return tuple(predictions), report


def _ranked_candidate_ids(row) -> tuple[tuple[str, ...], str]:
    rewritten = str(row.get("rewrite_status")) == "REWRITTEN"
    weights = (
        {query_replay.RAW_SOURCE: 0.0, query_replay.STANDALONE_SOURCE: 1.0}
        if rewritten
        else {query_replay.RAW_SOURCE: 1.0}
    )
    rankings = {
        source: tuple(map(str, (row.get("source_rankings") or {}).get(source, ())))
        for source in weights
    }
    ranked = fuse_rankings(
        rankings,
        weights=weights,
        rrf_k=query_replay.RRF_K,
        top_k=query_replay.CANDIDATE_K,
    )
    route = query_replay.STANDALONE_SOURCE if rewritten else query_replay.RAW_SOURCE
    return tuple(ranked), route


def _restore_candidates(row, ranked_ids, route, documents, manifest_fingerprint):
    metadata = {str(item["chunk_id"]): item for item in row.get("candidates") or ()}
    result = []
    for rank, chunk_id in enumerate(ranked_ids, 1):
        item = metadata.get(chunk_id)
        if item is None:
            raise ValueError(f"missing candidate metadata: {chunk_id}")
        document_id = str(item["document_id"])
        document = documents.get(document_id)
        if document is None:
            raise ValueError(f"missing candidate document: {document_id}")
        source = SourceDocument.create(
            source_id=document.document_id,
            title=document.title or document.document_id,
            content=document.content,
            source_type=str(document.metadata.get("source_type", "text")),
        )
        if source.checksum != str(item["source_checksum"]) or source.revision_id != str(
            item["source_revision"]
        ):
            raise ValueError(f"candidate source binding drift: {chunk_id}")
        start, end = int(item["source_start_char"]), int(item["source_end_char"])
        if not 0 <= start < end <= len(document.content):
            raise ValueError(f"candidate source span drift: {chunk_id}")
        result.append(
            ContextCandidate(
                chunk_id=chunk_id,
                document_id=document_id,
                text=document.content[start:end],
                start_char=start,
                end_char=end,
                title=document.title,
                score=1.0 / (query_replay.RRF_K + rank),
                ranks=((route, rank),),
                source_type=str(document.metadata.get("source_type", "text")),
                source_checksum=source.checksum,
                source_revision=source.revision_id,
                index_manifest_fingerprint=manifest_fingerprint,
            )
        )
    return tuple(result)


def _hits(candidates):
    return {
        item.chunk_id: {
            "document_id": item.document_id,
            "source_start_char": item.start_char,
            "source_end_char": item.end_char,
        }
        for item in candidates
    }


def _score(case, ranked_ids, hits, top_k):
    return evaluate_ranked_hits(case, ranked_ids, hits, top_k=top_k)


def _build_report(run_id, predictions):
    candidate = _aggregate_metrics(
        [item["candidate_metrics"] for item in predictions],
        "_at_20",
    )
    configs = []
    for config in SELECTION_CONFIGS:
        rows = [
            next(
                value
                for value in item["configurations"]
                if value["config_id"] == config.config_id
            )
            for item in predictions
        ]
        prepack = _aggregate_metrics([item["prepack_metrics"] for item in rows], "")
        packed = _aggregate_metrics([item["packed_metrics"] for item in rows], "")
        tokens = [int(item["token_count"]) for item in rows]
        latencies = [float(item["packing_cpu_latency_ms"]) for item in rows]
        configs.append(
            {
                **asdict(config),
                "prepack": prepack,
                "packed": packed,
                "candidate_to_prepack_evidence_loss": (
                    candidate["evidence_recall"] - prepack["evidence_recall"]
                ),
                "prepack_to_packed_evidence_loss": (
                    prepack["evidence_recall"] - packed["evidence_recall"]
                ),
                "packing_helpful_count": sum(
                    item["packing_change"] == "HELPFUL" for item in rows
                ),
                "packing_harmful_count": sum(
                    item["packing_change"] == "HARMFUL" for item in rows
                ),
                "mean_tokens": statistics.fmean(tokens),
                "p95_tokens": _p95(tokens),
                "mean_packing_cpu_latency_ms": statistics.fmean(latencies),
                "p95_packing_cpu_latency_ms": _p95(latencies),
            }
        )
    selected = min(
        configs,
        key=lambda item: (
            -item["packed"]["all_evidence_recall"]["rate"],
            -item["packed"]["evidence_recall"],
            item["mean_tokens"],
            item["config_id"],
        ),
    )
    return {
        "schema_version": 1,
        "runner_version": "knowledge-selection-packing-replay-v1",
        "run_id": run_id,
        "run_status": "COMPLETED",
        "evaluation_role": "VIEWED_DEV_SELECTION_PACKING",
        "case_count": len(predictions),
        "candidate": candidate,
        "configs": configs,
        "selection_order": [
            "packed_all_evidence_recall",
            "packed_evidence_recall",
            "mean_tokens",
            "config_id",
        ],
        "selected_config": {
            key: selected[key] for key in ("config_id", "final_k", "context_max_tokens")
        },
    }


def _aggregate_metrics(values, suffix):
    evidence = [float(item["evidence_recall"]) for item in values]
    key = f"all_evidence_recall{suffix}"
    passed = sum(value == 1.0 for value in evidence)
    return {
        key: {"passed": passed, "total": len(values), "rate": passed / len(values)},
        "evidence_recall": statistics.fmean(evidence),
        "mrr": statistics.fmean(float(item["mrr"]) for item in values),
        "ndcg": statistics.fmean(float(item["ndcg"]) for item in values),
    }


def _p95(values):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
