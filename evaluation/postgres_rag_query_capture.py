"""Capture fixed dense Raw/Standalone routes, then replay query weights."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from application.hybrid_retrieval import RetrievalGeneration
from application.knowledge_retriever import (
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
)
from core.model_policy import ModelPolicy
from evaluation.rag_query_artifacts import build_query_manifest
from evaluation.rag_query_replay import (
    CANDIDATE_K,
    RAW_SOURCE,
    RRF_K,
    SOURCE_K,
    STANDALONE_SOURCE,
    replay_query_grid,
)
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.reused_query_capture import (
    ReusedStandaloneCapture,
    rewrite_status,
)
from infrastructure.postgres_knowledge_retriever import (
    PostgresKnowledgeCandidateSource,
)
from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION


def build_query_source_capture_policy(
    generation: RetrievalGeneration,
) -> KnowledgeRetrievalPolicy:
    """Pin dense-only source capture without applying query fusion online."""
    return KnowledgeRetrievalPolicy(
        policy_version="knowledge-query-source-capture-v1",
        backend_fingerprint=generation.backend_fingerprint,
        lexical_provider=(
            f"{generation.chinese_tokenizer}+{generation.lexical_ranker}"
        ),
        transformer_version=QUERY_TRANSFORM_PROMPT_VERSION,
        embedding_version=generation.embedding_profile.fingerprint,
        reranker_version="not-run-query-stage-v1",
        packer_version="not-run-query-stage-v1",
        raw_query_weight=0.5,
        standalone_query_weight=0.5,
        dense_weight=1.0,
        lexical_weight=0.0,
        rrf_k=RRF_K,
        candidate_k=SOURCE_K,
        final_k=CANDIDATE_K,
    )


async def capture_postgres_query_sources_and_replay(
    *,
    dataset: RagDataset,
    reused_capture: ReusedStandaloneCapture,
    tenant_id: str,
    locale: str,
    generation: RetrievalGeneration,
    candidate_source: PostgresKnowledgeCandidateSource,
    chunk_count: int,
    index_prepare_latency_ms: float,
    output_dir: Path | str,
    run_id: str,
    current_model_policy: ModelPolicy,
) -> dict[str, Any]:
    """Persist Raw/Standalone dense routes once and replay four Dev configs."""
    if reused_capture.split != "dev":
        raise ValueError("query parameter selection is restricted to Dev")
    cases = {case.case_id: case for case in dataset.select_cases("dev")}
    policy = build_query_source_capture_policy(generation)
    rows: list[dict[str, Any]] = []

    for captured in reused_capture.rows:
        case = cases[str(captured["case_id"])]
        raw_query = str(captured["raw_query"])
        standalone = str(captured["standalone"])
        query_status = rewrite_status(captured)
        variants = [("raw", raw_query, 1.0)]
        if query_status == "REWRITTEN":
            variants.append(("standalone", standalone, 1.0))
        request = KnowledgeRetrievalRequest(
            tenant_id=tenant_id,
            user_scope="public-evaluation",
            authorization_fingerprint="public-evaluation-v1",
            acl_policy_fingerprint="knowledge-public-only-v1",
            deletion_epoch=0,
            requirement_signature="knowledge.query_source_capture",
            query=raw_query,
            history=case.history,
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
            variants,
            dense_k=SOURCE_K,
            lexical_k=0,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        candidates = list(result.candidates)
        rows.append(
            {
                "schema_version": 1,
                "case_id": case.case_id,
                "group_id": case.group_id,
                "query": case.query,
                "raw_query": raw_query,
                "standalone": standalone,
                "history_turns": len(case.history),
                "rewrite_status": query_status,
                "standalone_errors": [
                    str(error)
                    for error in captured.get("errors") or ()
                    if str(error).startswith("standalone:")
                ],
                "answerable": case.answerable,
                "retrieval_status": result.status.value,
                "detail_code": result.detail_code,
                "latency_ms": latency_ms,
                "source_rankings": {
                    source: _ranked_ids(candidates, source)
                    for source in (RAW_SOURCE, STANDALONE_SOURCE)
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
    predictions_path = output / "predictions.jsonl"
    _write_jsonl(predictions_path, rows)
    report = replay_query_grid(
        dataset=dataset,
        capture_rows=_read_jsonl(predictions_path),
        run_id=run_id,
    )
    latencies = [float(row["latency_ms"]) for row in rows]
    report["source_capture"] = {
        "artifact": predictions_path.name,
        "sha256": _file_sha256(predictions_path),
        "latency_ms": {
            "mean": statistics.fmean(latencies),
            "p95": _nearest_rank_percentile(latencies, 0.95),
            "max": max(latencies),
            "percentile_method": "nearest_rank",
        },
    }
    manifest = build_query_manifest(
        dataset=dataset,
        reused_capture=reused_capture,
        generation=generation,
        policy=policy,
        current_model_policy=current_model_policy,
        chunk_count=chunk_count,
        index_prepare_latency_ms=index_prepare_latency_ms,
        report=report,
        run_id=run_id,
    )
    _write_json(output / "manifest.json", manifest)
    _write_json(output / "report.json", report)
    return report


def _ranked_ids(candidates: list[Mapping[str, Any]], source: str) -> list[str]:
    return [
        str(item["chunk_id"])
        for item in sorted(
            candidates,
            key=lambda item: int(item.get("ranks", {}).get(source, SOURCE_K + 1)),
        )
        if source in item.get("ranks", {})
    ]


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
