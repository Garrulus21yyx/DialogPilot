"""Raw-query candidate evaluation over the production Knowledge candidate port."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from application.hybrid_retrieval import RetrievalGeneration, RetrievalStatus
from application.knowledge_retriever import (
    KnowledgeCandidateSource,
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
)
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.document_chunker import ChunkStrategy


@dataclass(frozen=True)
class CandidateChunkProfile:
    strategy: str
    max_tokens: int
    overlap_tokens: int


CHUNK_PROFILES: Mapping[str, CandidateChunkProfile] = {
    "structure-256-32": CandidateChunkProfile(
        ChunkStrategy.STRUCTURE_AWARE.value, 256, 32,
    ),
    "structure-384-48": CandidateChunkProfile(
        ChunkStrategy.STRUCTURE_AWARE.value, 384, 48,
    ),
    "structure-512-64": CandidateChunkProfile(
        ChunkStrategy.STRUCTURE_AWARE.value, 512, 64,
    ),
    "fixed-512-64": CandidateChunkProfile(
        ChunkStrategy.FIXED_TOKENS.value, 512, 64,
    ),
}

CANDIDATE_K = 20
DENSE_WEIGHT = 0.25
LEXICAL_WEIGHT = 0.75
RRF_K = 10
RUNNER_VERSION = "postgres-bge-raw-candidate-eval-v1"


def build_candidate_policy(
    generation: RetrievalGeneration,
) -> KnowledgeRetrievalPolicy:
    """Pin the existing online fusion owner to this raw-only experiment."""
    return KnowledgeRetrievalPolicy(
        policy_version="knowledge-raw-candidate-eval-v1",
        backend_fingerprint=generation.backend_fingerprint,
        lexical_provider=(
            f"{generation.chinese_tokenizer}+{generation.lexical_ranker}"
        ),
        transformer_version="raw-query-only-v1",
        embedding_version=generation.embedding_profile.fingerprint,
        reranker_version="not-run-candidate-stage-v1",
        packer_version="not-run-candidate-stage-v1",
        raw_query_weight=1.0,
        standalone_query_weight=0.0,
        dense_weight=DENSE_WEIGHT,
        lexical_weight=LEXICAL_WEIGHT,
        rrf_k=RRF_K,
        candidate_k=CANDIDATE_K,
        final_k=5,
    )


async def evaluate_postgres_candidates(
    *,
    dataset: RagDataset,
    split: str,
    tenant_id: str,
    locale: str,
    generation: RetrievalGeneration,
    candidate_source: KnowledgeCandidateSource,
    chunk_profile_id: str,
    chunk_count: int,
    index_prepare_latency_ms: float,
    output_dir: Path | str,
    run_id: str,
) -> dict[str, Any]:
    """Run only ``raw query -> PG candidate retrieval`` and write artifacts."""
    cases = dataset.select_cases(split)
    if not cases:
        raise ValueError(f"dataset split has no cases: {split}")
    if not any(case.answerable for case in cases):
        raise ValueError("candidate recall evaluation requires answerable cases")
    profile = CHUNK_PROFILES[chunk_profile_id]
    policy = build_candidate_policy(generation)
    predictions: list[dict[str, Any]] = []

    for case in cases:
        request = KnowledgeRetrievalRequest(
            tenant_id=tenant_id,
            user_scope="public-evaluation",
            authorization_fingerprint="public-evaluation-v1",
            acl_policy_fingerprint="knowledge-public-only-v1",
            deletion_epoch=0,
            requirement_signature="knowledge.candidate_retrieval",
            query=case.query,
            history=(),
            conversation_range_hash=_sha256(case.case_id),
            locale=locale,
            product=None,
            manifest_fingerprint=generation.manifest_hash,
            generation_id=generation.generation_id,
            policy=policy,
            force_recompute=True,
        )
        started = time.perf_counter()
        result = await candidate_source.search_variants_async(
            request,
            [("raw", case.query, 1.0)],
            top_k=policy.candidate_k,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        candidates = list(result.candidates)
        ranked_ids = [str(item["chunk_id"]) for item in candidates]
        hit_by_id = {
            str(item["chunk_id"]): {
                "document_id": str(item["source_id"]),
                "source_start_char": int(item["source_start_char"]),
                "source_end_char": int(item["source_end_char"]),
            }
            for item in candidates
        }
        metrics = evaluate_ranked_hits(
            case, ranked_ids, hit_by_id, top_k=policy.candidate_k,
        )
        predictions.append({
            "case_id": case.case_id,
            "group_id": case.group_id,
            "query": case.query,
            "query_mode": "raw_only",
            "answerable": case.answerable,
            "retrieval_status": result.status.value,
            "detail_code": result.detail_code,
            "latency_ms": latency_ms,
            "metrics": metrics,
            "all_evidence_recalled": bool(
                case.answerable and metrics["evidence_recall"] == 1.0
            ),
            "candidates": [
                {
                    "rank": rank,
                    "chunk_id": str(item["chunk_id"]),
                    "document_id": str(item["source_id"]),
                    "source_revision": str(item["source_revision"]),
                    "source_checksum": str(item["source_checksum"]),
                    "source_start_char": int(item["source_start_char"]),
                    "source_end_char": int(item["source_end_char"]),
                    "score": float(item["score"]),
                    "source_ranks": dict(item["ranks"]),
                }
                for rank, item in enumerate(candidates, 1)
            ],
        })

    report = _report(run_id, split, predictions, policy.candidate_k)
    manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_scope": "knowledge_candidate_only",
        "stages_executed": ["raw_query", "candidate_retrieval"],
        "stages_not_run": [
            "standalone_query", "rerank", "parent_expansion", "packing",
            "generation", "judge",
        ],
        "dataset": {
            "dataset_id": str(dataset.manifest["dataset_id"]),
            "split": split,
            "case_count": len(cases),
            "document_count": len(dataset.documents),
            "corpus_sha256": str(dataset.manifest["corpus_sha256"]),
            "cases_sha256": str(dataset.manifest["cases_sha256"]),
        },
        "chunk_profile": {
            "profile_id": chunk_profile_id,
            **asdict(profile),
            "chunk_count": chunk_count,
        },
        "query": {"mode": "raw_only", "history_consumed": False},
        "retrieval_scope": {
            "tenant_id": tenant_id,
            "scope": "public",
            "locale": locale,
            "product": None,
        },
        "candidate_policy": {**asdict(policy), "fingerprint": policy.fingerprint},
        "embedding_profile": _embedding_profile(generation),
        "generation": _generation_identity(generation),
        "index_prepare_latency_ms": index_prepare_latency_ms,
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "manifest.json", manifest)
    _write_jsonl(output / "predictions.jsonl", predictions)
    _write_json(output / "report.json", report)
    return report


def _report(
    run_id: str,
    split: str,
    predictions: list[dict[str, Any]],
    candidate_k: int,
) -> dict[str, Any]:
    scored = [item for item in predictions if item["answerable"]]
    latencies = [float(item["latency_ms"]) for item in predictions]
    metrics = {
        key: statistics.fmean(float(item["metrics"][key]) for item in scored)
        for key in ("evidence_recall", "document_recall", "mrr", "ndcg")
    }
    status_counts: dict[str, int] = {}
    for item in predictions:
        status = str(item["retrieval_status"])
        status_counts[status] = status_counts.get(status, 0) + 1
    system_failures = sum(
        count for status, count in status_counts.items()
        if status not in {RetrievalStatus.OK.value, RetrievalStatus.NO_EVIDENCE.value}
    )
    all_evidence = sum(bool(item["all_evidence_recalled"]) for item in scored)
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "run_status": "COMPLETED" if not system_failures else "COMPLETED_WITH_ERRORS",
        "evaluation_scope": "knowledge_candidate_only",
        "query_mode": "raw_only",
        "split": split,
        "case_count": len(predictions),
        "scored_case_count": len(scored),
        "candidate_k": candidate_k,
        "candidate_metrics": metrics,
        "all_evidence_recall": {
            "passed": all_evidence,
            "total": len(scored),
            "rate": all_evidence / len(scored),
        },
        "retrieval_status_counts": status_counts,
        "system_failure_count": system_failures,
        "retrieval_latency_ms": {
            "mean": statistics.fmean(latencies),
            "p95": _nearest_rank_percentile(latencies, 0.95),
            "max": max(latencies),
            "percentile_method": "nearest_rank",
        },
        "excluded_stages": [
            "standalone_query", "rerank", "parent_expansion", "packing",
            "generation", "judge",
        ],
    }


def _embedding_profile(generation: RetrievalGeneration) -> dict[str, Any]:
    profile = generation.embedding_profile
    return {
        "provider": profile.provider,
        "provider_kind": profile.provider_kind.value,
        "model": profile.model,
        "model_version": profile.model_version,
        "dimension": profile.dimension,
        "model_digest": profile.model_digest,
        "document_preprocessing": profile.document_preprocessing,
        "query_preprocessing": profile.query_preprocessing,
        "fingerprint": profile.fingerprint,
    }


def _generation_identity(generation: RetrievalGeneration) -> dict[str, Any]:
    return {
        "generation_id": generation.generation_id,
        "state": generation.state.value,
        "corpus": generation.corpus.value,
        "backend_id": generation.backend_id,
        "backend_fingerprint": generation.backend_fingerprint,
        "schema_version": generation.schema_version,
        "source_watermark": generation.source_watermark,
        "manifest_fingerprint": generation.manifest_hash,
        "immutable_fingerprint": generation.immutable_fingerprint(),
        "index_method": generation.index_method,
        "index_params": json.loads(generation.index_params_json),
    }


def _nearest_rank_percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
