"""Serialize heldout retrieval identities without owning evaluation policy."""

from __future__ import annotations

import json

from mcp.context_packer import ContextCandidate


def build_heldout_prediction(
    run_id,
    case,
    rewrite,
    kind,
    result,
    retrieval_latency_ms,
    *,
    source_candidates,
    candidate_ids,
    candidate_metrics,
    prepack_ids,
    prepack_metrics,
    evidence_pack,
    packed_metrics,
    token_count,
    packing_latency_ms,
):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": case.case_id,
        "group_id": case.group_id,
        "domain": next(
            item
            for item in case.query_types
            if item in {"dmv", "ssa", "studentaid", "va"}
        ),
        "document_length": next(
            item for item in case.query_types if item.endswith("_document")
        ),
        "history_turns": len(case.history),
        "rewrite": {
            "status": rewrite.status,
            "standalone": rewrite.standalone,
            "error": rewrite.error,
            "usage": dict(rewrite.usage),
            "provider_request_ids": list(rewrite.provider_request_ids),
        },
        "selected_query_route": f"{kind}:vector",
        "retrieval_status": result.status.value,
        "detail_code": result.detail_code,
        "retrieval_latency_ms": retrieval_latency_ms,
        "source_candidates": [candidate_projection(item) for item in source_candidates],
        "candidate_ids": list(candidate_ids),
        "candidate_metrics": candidate_metrics,
        "prepack_ids": list(prepack_ids),
        "prepack_metrics": prepack_metrics,
        "evidence_pack": (
            evidence_pack.to_dict(include_text=False) if evidence_pack else None
        ),
        "packed_metrics": packed_metrics,
        "packing_change": packing_change(prepack_metrics, packed_metrics),
        "token_count": token_count,
        "packing_cpu_latency_ms": packing_latency_ms,
    }


def candidate_to_context(item, *, route, manifest_fingerprint, rrf_k):
    rank = int(item["ranks"][route])
    return ContextCandidate(
        chunk_id=str(item["chunk_id"]),
        document_id=str(item["source_id"]),
        text=str(item["content"]),
        start_char=int(item["source_start_char"]),
        end_char=int(item["source_end_char"]),
        title=str(item.get("title") or ""),
        score=1.0 / (rrf_k + rank),
        ranks=tuple(
            sorted((str(key), int(value)) for key, value in item["ranks"].items())
        ),
        source_type=str(item["source_type"]),
        source_checksum=str(item["source_checksum"]),
        source_revision=str(item["source_revision"]),
        scope=str(item["scope"]),
        scope_decision=str(item["scope_decision"]),
        index_manifest_fingerprint=manifest_fingerprint,
    )


def candidate_projection(item):
    return {
        "rank": min(map(int, item["ranks"].values())),
        "chunk_id": str(item["chunk_id"]),
        "document_id": str(item["source_id"]),
        "source_revision": str(item["source_revision"]),
        "source_checksum": str(item["source_checksum"]),
        "source_start_char": int(item["source_start_char"]),
        "source_end_char": int(item["source_end_char"]),
        "source_ranks": dict(item["ranks"]),
    }


def packing_change(before, after):
    delta = float(after["evidence_recall"]) - float(before["evidence_recall"])
    return "HELPFUL" if delta > 0 else "HARMFUL" if delta < 0 else "NEUTRAL"


def embedding_profile_projection(generation):
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


def generation_identity_projection(generation):
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
