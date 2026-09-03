"""Manifest projection for the bounded Raw/Standalone Dev experiment."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from application.hybrid_retrieval import RetrievalGeneration
from application.knowledge_retriever import KnowledgeRetrievalPolicy
from core.model_policy import ModelPolicy, ModelRole
from evaluation.postgres_rag_candidate_eval import CHUNK_PROFILES
from evaluation.rag_query_replay import (
    CANDIDATE_K,
    QUERY_CONFIGS,
    RAW_SOURCE,
    RRF_K,
    SOURCE_K,
    STANDALONE_SOURCE,
)
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.reused_query_capture import ReusedStandaloneCapture


CHUNK_PROFILE_ID = "fixed-512-64"
RUNNER_VERSION = "postgres-bge-reused-query-capture-replay-v1"


def build_query_manifest(
    *,
    dataset: RagDataset,
    reused_capture: ReusedStandaloneCapture,
    generation: RetrievalGeneration,
    policy: KnowledgeRetrievalPolicy,
    current_model_policy: ModelPolicy,
    chunk_count: int,
    index_prepare_latency_ms: float,
    report: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    profile = CHUNK_PROFILES[CHUNK_PROFILE_ID]
    current_rewrite = current_model_policy.profile(ModelRole.REWRITE)
    return {
        "schema_version": 1,
        "runner_version": RUNNER_VERSION,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_role": "DEV_QUERY_SELECTION_REUSED_CAPTURE",
        "evaluation_scope": "knowledge_dense_raw_standalone_replay_only",
        "stages_executed": [
            "reuse_captured_raw_and_standalone_queries",
            "raw_dense_top_40",
            "standalone_dense_top_40_when_rewritten",
            "offline_query_rrf_replay",
        ],
        "stages_not_run": ["live_query_rewrite", *report["excluded_stages"]],
        "dataset": {
            "dataset_id": str(dataset.manifest["dataset_id"]),
            "split": "dev",
            "split_case_count": len(dataset.select_cases("dev")),
            "document_count": len(dataset.documents),
            "corpus_sha256": str(dataset.manifest["corpus_sha256"]),
            "cases_sha256": str(dataset.manifest["cases_sha256"]),
        },
        "cohort": {
            "case_count": len(reused_capture.rows),
            "case_ids_sha256": _sha256(
                "\n".join(sorted(str(row["case_id"]) for row in reused_capture.rows))
            ),
            "status": "DESIGNATED_DEV_PREVIOUSLY_VIEWED",
            "selection_policy": reused_capture.sample_policy,
            "equivalent_to_fusion_dev_300": False,
            "note": (
                "This reused 48-case query cohort is not the 300-case "
                "fusion-selection cohort and is not heldout evidence."
            ),
        },
        "reused_query_transform": {
            "owner": "mcp.query_transformer.QueryTransformer",
            "artifact": reused_capture.path.name,
            "sha256": reused_capture.sha256,
            "prompt_version": reused_capture.prompt_version,
            "generated_this_run": False,
            "inspected_dev": True,
            "consumed_fields": [
                "raw_query",
                "standalone",
                "standalone-prefixed errors",
            ],
            "ignored_fields": ["expansions", "hyde"],
            "captured_model_policy": dict(reused_capture.captured_model_policy),
            "current_validated_profile": {
                "provider": current_model_policy.provider,
                "base_url": current_model_policy.base_url or "official",
                **current_rewrite.to_dict(),
            },
            "historical_usage": dict(reused_capture.original_usage),
            "current_run_calls": 0,
        },
        "chunk_profile": {
            "profile_id": CHUNK_PROFILE_ID,
            **asdict(profile),
            "chunk_count": chunk_count,
        },
        "source_capture": {
            "dense_k": SOURCE_K,
            "lexical_k": 0,
            "sources": [RAW_SOURCE, STANDALONE_SOURCE],
            "fusion_applied": False,
            "artifact": report["source_capture"]["artifact"],
            "sha256": report["source_capture"]["sha256"],
        },
        "offline_replay": {
            "candidate_k": CANDIDATE_K,
            "rrf_k": RRF_K,
            "rrf_k_origin": "stable_tiebreak_not_identified_in_dense_only_stage",
            "configs": [asdict(config) for config in QUERY_CONFIGS],
            "selection_order": report["selection_order"],
        },
        "capture_policy": {**asdict(policy), "fingerprint": policy.fingerprint},
        "embedding_profile": _embedding_profile(generation),
        "generation": _generation_identity(generation),
        "index_prepare_latency_ms": index_prepare_latency_ms,
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


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
