"""Production-aligned Knowledge retrieval evaluation on sealed heldout cases."""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict
from typing import Any, Mapping, Protocol, Sequence

from application.hybrid_retrieval import RetrievalGeneration, RetrievalStatus
from application.knowledge_retriever import (
    EvidencePackResult,
    KnowledgeCandidateResult,
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
    KnowledgeRetriever,
)
from core.llm_metrics import capture_llm_usage
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.context_packer import ContextPacker
from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION
from mcp.result_reranker import RERANK_PROMPT_VERSION
from memory.context import TokenEstimator


RUNNER_VERSION = "postgres-production-rag-heldout-v2"
SOURCE_K = 20
CANDIDATE_K = 20
FINAL_K = 5
CONTEXT_MAX_TOKENS = 2600
RRF_K = 10


class CandidateSource(Protocol):
    async def search_variants_async(
        self, request, variants, *, top_k,
    ) -> KnowledgeCandidateResult: ...


class Reranker(Protocol):
    async def rerank(self, query, candidates) -> tuple[tuple[str, ...], bool]: ...


def build_production_eval_policy(
    generation: RetrievalGeneration,
) -> KnowledgeRetrievalPolicy:
    """Freeze the production configuration before opening heldout outcomes."""
    return KnowledgeRetrievalPolicy(
        policy_version="knowledge-production-heldout-v2",
        backend_fingerprint=generation.backend_fingerprint,
        lexical_provider=generation.lexical_ranker,
        transformer_version=QUERY_TRANSFORM_PROMPT_VERSION,
        embedding_version=generation.embedding_profile.fingerprint,
        reranker_version=RERANK_PROMPT_VERSION,
        packer_version="context-packer-v1",
        raw_query_weight=0.20,
        standalone_query_weight=0.60,
        expansion_query_weight=0.20,
        query_expansion_count=2,
        metadata_hint_weight=0.50,
        dense_weight=0.25,
        lexical_weight=0.75,
        rrf_k=RRF_K,
        candidate_k=CANDIDATE_K,
        final_k=FINAL_K,
        context_max_tokens=CONTEXT_MAX_TOKENS,
    )


class CapturingCandidateSource:
    def __init__(self, delegate: CandidateSource):
        self.delegate = delegate
        self.result: KnowledgeCandidateResult | None = None
        self.variants: tuple[tuple[str, str, float], ...] = ()

    def reset(self) -> None:
        self.result = None
        self.variants = ()

    async def search_variants_async(self, request, variants, *, top_k):
        self.variants = tuple(variants)
        self.result = await self.delegate.search_variants_async(
            request, variants, top_k=top_k,
        )
        return self.result


class CapturingReranker:
    def __init__(self, delegate: Reranker):
        self.delegate = delegate
        self.ordered_ids: tuple[str, ...] = ()
        self.fallback = False

    def reset(self) -> None:
        self.ordered_ids = ()
        self.fallback = False

    async def rerank(self, query, candidates):
        self.ordered_ids, self.fallback = await self.delegate.rerank(
            query, candidates,
        )
        return self.ordered_ids, self.fallback


async def evaluate_production_heldout(
    *,
    dataset: RagDataset,
    tenant_id: str,
    locale: str,
    generation: RetrievalGeneration,
    candidate_source: CandidateSource,
    transformer,
    reranker: Reranker,
    run_id: str,
    expected_case_count: int,
) -> tuple[dict[str, Any], ...]:
    cases = validate_fresh_heldout_dataset(
        dataset, expected_case_count=expected_case_count,
    )
    policy = build_production_eval_policy(generation)
    captured_source = CapturingCandidateSource(candidate_source)
    captured_reranker = CapturingReranker(reranker)
    retriever = KnowledgeRetriever(
        candidate_source=captured_source,
        transformer=transformer,
        reranker=captured_reranker,
        packer=ContextPacker(),
        cache=None,
        evidence_validator=None,
    )
    predictions = []
    for case in cases:
        captured_source.reset()
        captured_reranker.reset()
        request = _request(
            case, tenant_id, locale, generation, policy,
        )
        started = time.perf_counter()
        with capture_llm_usage() as usage:
            result = await retriever.retrieve(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        predictions.append(_prediction(
            run_id=run_id,
            case=case,
            result=result,
            source_result=captured_source.result,
            variants=captured_source.variants,
            reranked_ids=captured_reranker.ordered_ids,
            rerank_fallback=captured_reranker.fallback,
            usage=usage.summary(),
            elapsed_ms=elapsed_ms,
        ))
    return tuple(predictions)


def validate_fresh_heldout_dataset(
    dataset: RagDataset, *, expected_case_count: int,
):
    cases = dataset.select_cases("heldout")
    if len(cases) != expected_case_count:
        raise ValueError("fresh heldout case-count drift")
    if len({case.group_id for case in cases}) != len(cases):
        raise ValueError("fresh heldout must contain one case per conversation")
    if not cases or not all(case.answerable and case.evidence for case in cases):
        raise ValueError("fresh heldout requires grounded answerable cases")
    source = dataset.manifest.get("source") or {}
    if (
        source.get("official_split") != "test"
        or source.get("corpus_policy") != "all_official_documents"
        or not str(dataset.manifest.get("dataset_id") or "").startswith(
            "doc2dial-rag-en-heldout-"
        )
    ):
        raise ValueError("dataset is not a sealed Doc2Dial test cohort")
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _request(case, tenant_id, locale, generation, policy):
    return KnowledgeRetrievalRequest(
        tenant_id=tenant_id,
        user_scope="public-evaluation",
        authorization_fingerprint="public-evaluation-v2",
        acl_policy_fingerprint="knowledge-public-only-v1",
        deletion_epoch=0,
        requirement_signature="knowledge.fresh_heldout_v2",
        query=case.query,
        history=case.history,
        conversation_range_hash=_sha256(case.case_id),
        locale=locale,
        product=None,
        manifest_fingerprint=generation.manifest_hash,
        generation_id=generation.generation_id,
        policy=policy,
        force_recompute=True,
    )


def _prediction(
    *, run_id, case, result, source_result, variants, reranked_ids,
    rerank_fallback, usage, elapsed_ms,
):
    candidates = (
        tuple(source_result.candidates)
        if source_result is not None
        and source_result.status is RetrievalStatus.OK
        else ()
    )
    hit_by_id = {
        str(item["chunk_id"]): {
            "document_id": str(item["source_id"]),
            "source_start_char": int(item["source_start_char"]),
            "source_end_char": int(item["source_end_char"]),
        }
        for item in candidates
    }
    candidate_ids = tuple(str(item["chunk_id"]) for item in candidates)
    candidate_metrics = evaluate_ranked_hits(
        case, candidate_ids, hit_by_id, top_k=CANDIDATE_K,
    )
    valid_rerank = (
        len(reranked_ids) == len(candidate_ids)
        and set(reranked_ids) == set(candidate_ids)
    )
    ordered_ids = reranked_ids if valid_rerank else candidate_ids
    prepack_ids = ordered_ids[:FINAL_K]
    prepack_metrics = evaluate_ranked_hits(
        case, prepack_ids, hit_by_id, top_k=FINAL_K,
    )
    packed_ids = ()
    evidence_pack = None
    token_count = 0
    if result.status is RetrievalStatus.OK and result.evidence_pack is not None:
        evidence_pack = result.evidence_pack.to_dict(include_text=False)
        packed_ids = tuple(item.chunk_id for item in result.evidence_pack.items)
        token_count = sum(
            TokenEstimator().estimate(item.text)
            for item in result.evidence_pack.items
        )
    packed_metrics = evaluate_ranked_hits(
        case, packed_ids, hit_by_id, top_k=FINAL_K,
    )
    return {
        "schema_version": 1,
        "run_id": run_id,
        "case_id": case.case_id,
        "group_id": case.group_id,
        "domain": _query_type(case, {"dmv", "ssa", "studentaid", "va"}),
        "document_length": next(
            item for item in case.query_types if item.endswith("_document")
        ),
        "history_turns": len(case.history),
        "retrieval_status": result.status.value,
        "detail_code": result.detail_code,
        "variants": [
            {"kind": kind, "query": query, "weight": weight}
            for kind, query, weight in variants
        ],
        "candidate_ids": list(candidate_ids),
        "candidate_metrics": candidate_metrics,
        "prepack_ids": list(prepack_ids),
        "prepack_metrics": prepack_metrics,
        "packed_ids": list(packed_ids),
        "packed_metrics": packed_metrics,
        "evidence_pack": evidence_pack,
        "rerank_fallback": bool(rerank_fallback or not valid_rerank),
        "packing_change": _packing_change(prepack_metrics, packed_metrics),
        "token_count": token_count,
        "end_to_end_latency_ms": elapsed_ms,
        "llm_usage": usage,
        "candidate_projections": [_candidate_projection(item) for item in candidates],
    }


def _candidate_projection(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": str(item["chunk_id"]),
        "document_id": str(item["source_id"]),
        "source_revision": str(item["source_revision"]),
        "source_start_char": int(item["source_start_char"]),
        "source_end_char": int(item["source_end_char"]),
        "source_ranks": dict(item["ranks"]),
    }


def _query_type(case, allowed: set[str]) -> str:
    return next(item for item in case.query_types if item in allowed)


def _packing_change(before, after) -> str:
    delta = float(after["evidence_recall"]) - float(before["evidence_recall"])
    return "HELPFUL" if delta > 0 else "HARMFUL" if delta < 0 else "NEUTRAL"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def policy_manifest(policy: KnowledgeRetrievalPolicy) -> dict[str, Any]:
    return {**asdict(policy), "fingerprint": policy.fingerprint}
