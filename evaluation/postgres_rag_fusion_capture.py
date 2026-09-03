"""Capture PG lexical/dense rankings once, then replay fusion offline."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from application.hybrid_retrieval import RetrievalGeneration
from application.knowledge_retriever import (
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
)
from evaluation.postgres_rag_candidate_eval import CHUNK_PROFILES
from evaluation.rag_fusion_replay import (
    DENSE_SOURCE,
    FINAL_K,
    FUSION_CONFIGS,
    LEXICAL_SOURCE,
    SOURCE_K,
    replay_fusion_grid,
)
from evaluation.rag_pipeline.dataset import RagDataset
from infrastructure.postgres_knowledge_retriever import (
    PostgresKnowledgeCandidateSource,
)


CHUNK_PROFILE_ID = "fixed-512-64"
RUNNER_VERSION = "postgres-bge-source-capture-fusion-replay-v1"


def build_source_capture_policy(
    generation: RetrievalGeneration,
) -> KnowledgeRetrievalPolicy:
    """Pin capture identity; fusion fields are not applied during capture."""
    return KnowledgeRetrievalPolicy(
        policy_version="knowledge-raw-source-capture-v1",
        backend_fingerprint=generation.backend_fingerprint,
        lexical_provider=(
            f"{generation.chinese_tokenizer}+{generation.lexical_ranker}"
        ),
        transformer_version="raw-query-only-v1",
        embedding_version=generation.embedding_profile.fingerprint,
        reranker_version="not-run-fusion-stage-v1",
        packer_version="not-run-fusion-stage-v1",
        raw_query_weight=1.0,
        standalone_query_weight=0.0,
        dense_weight=0.5,
        lexical_weight=0.5,
        rrf_k=10,
        candidate_k=SOURCE_K,
        final_k=FINAL_K,
    )


async def capture_postgres_sources_and_replay(
    *,
    dataset: RagDataset,
    split: str,
    tenant_id: str,
    locale: str,
    generation: RetrievalGeneration,
    candidate_source: PostgresKnowledgeCandidateSource,
    chunk_count: int,
    index_prepare_latency_ms: float,
    output_dir: Path | str,
    run_id: str,
) -> dict[str, Any]:
    """Persist unfused Top-40 routes and replay the bounded grid from disk."""
    if split != "dev":
        raise ValueError("fusion parameter selection is restricted to dev")
    cases = dataset.select_cases(split)
    if not cases:
        raise ValueError(f"dataset split has no cases: {split}")
    policy = build_source_capture_policy(generation)
    rows: list[dict[str, Any]] = []
    for case in cases:
        request = KnowledgeRetrievalRequest(
            tenant_id=tenant_id,
            user_scope="public-evaluation",
            authorization_fingerprint="public-evaluation-v1",
            acl_policy_fingerprint="knowledge-public-only-v1",
            deletion_epoch=0,
            requirement_signature="knowledge.fusion_source_capture",
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
        result = await candidate_source.capture_source_rankings_async(
            request,
            [("raw", case.query, 1.0)],
            source_k=SOURCE_K,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        candidates = list(result.candidates)
        rows.append(
            {
                "schema_version": 1,
                "case_id": case.case_id,
                "group_id": case.group_id,
                "query": case.query,
                "query_mode": "raw_only",
                "answerable": case.answerable,
                "retrieval_status": result.status.value,
                "detail_code": result.detail_code,
                "latency_ms": latency_ms,
                "source_rankings": {
                    source: [
                        str(item["chunk_id"])
                        for item in sorted(
                            candidates,
                            key=lambda item: int(
                                item.get("ranks", {}).get(
                                    source,
                                    SOURCE_K + 1,
                                )
                            ),
                        )
                        if source in item.get("ranks", {})
                    ]
                    for source in (LEXICAL_SOURCE, DENSE_SOURCE)
                },
                "candidates": [
                    {
                        "chunk_id": str(item["chunk_id"]),
                        "document_id": str(item["source_id"]),
                        "source_revision": str(item["source_revision"]),
                        "source_checksum": str(item["source_checksum"]),
                        "source_start_char": int(item["source_start_char"]),
                        "source_end_char": int(item["source_end_char"]),
                    }
                    for item in candidates
                ],
            }
        )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    capture_path = output / "source_capture.jsonl"
    _write_jsonl(capture_path, rows)
    persisted_rows = _read_jsonl(capture_path)
    report = replay_fusion_grid(
        dataset=dataset,
        split=split,
        capture_rows=persisted_rows,
        run_id=run_id,
    )
    latencies = [float(row["latency_ms"]) for row in rows]
    report["source_capture"] = {
        "artifact": capture_path.name,
        "sha256": _file_sha256(capture_path),
        "latency_ms": {
            "mean": statistics.fmean(latencies),
            "p95": _nearest_rank_percentile(latencies, 0.95),
            "max": max(latencies),
            "percentile_method": "nearest_rank",
        },
    }
    profile = CHUNK_PROFILES[CHUNK_PROFILE_ID]
    manifest = {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "DEV_FUSION_SELECTION",
        "evaluation_scope": "knowledge_source_capture_offline_fusion_only",
        "stages_executed": [
            "raw_query",
            "lexical_top_40",
            "dense_top_40",
            "offline_rrf_replay",
        ],
        "stages_not_run": report["excluded_stages"],
        "dataset": {
            "dataset_id": str(dataset.manifest["dataset_id"]),
            "split": split,
            "case_count": len(cases),
            "document_count": len(dataset.documents),
            "corpus_sha256": str(dataset.manifest["corpus_sha256"]),
            "cases_sha256": str(dataset.manifest["cases_sha256"]),
        },
        "chunk_profile": {
            "profile_id": CHUNK_PROFILE_ID,
            **asdict(profile),
            "chunk_count": chunk_count,
        },
        "query": {"mode": "raw_only", "history_consumed": False},
        "source_capture": {
            "source_k": SOURCE_K,
            "sources": [LEXICAL_SOURCE, DENSE_SOURCE],
            "fusion_applied": False,
            "artifact": capture_path.name,
            "sha256": report["source_capture"]["sha256"],
        },
        "offline_replay": {
            "candidate_k": FINAL_K,
            "configs": [asdict(config) for config in FUSION_CONFIGS],
            "selection_order": report["selection_order"],
        },
        "capture_policy": {**asdict(policy), "fingerprint": policy.fingerprint},
        "embedding_profile": _embedding_profile(generation),
        "generation": _generation_identity(generation),
        "index_prepare_latency_ms": index_prepare_latency_ms,
    }
    _write_json(output / "manifest.json", manifest)
    _write_json(output / "report.json", report)
    return report


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


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        ),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
