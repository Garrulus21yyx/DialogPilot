"""Fixed Knowledge candidate-to-packing evaluation on frozen heldout cases."""

from __future__ import annotations

import hashlib
import time
from typing import Any, Protocol

from application.hybrid_retrieval import RetrievalGeneration, RetrievalStatus
from application.knowledge_retriever import (
    KnowledgeCandidateResult,
    KnowledgeQueryTransformer,
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
)
from evaluation.rag_heldout_projections import (
    build_heldout_prediction,
    candidate_to_context,
)
from evaluation.rag_heldout_rewrite import capture_rewrites
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.context_packer import CONTEXT_PACKER_VERSION, ContextPacker
from mcp.evidence_pack import EvidencePack
from mcp.query_transformer import QUERY_TRANSFORM_PROMPT_VERSION


SOURCE_K = 40
CANDIDATE_K = 20
FINAL_K = 5
CONTEXT_MAX_TOKENS = 2600
RRF_K = 10
CHUNK_PROFILE_ID = "fixed-512-64"
RUNNER_VERSION = "postgres-bge-heldout-fixed-baseline-v1"


class HeldoutCandidateSource(Protocol):
    async def capture_source_rankings_async(
        self,
        request: KnowledgeRetrievalRequest,
        variants: list[tuple[str, str, float]],
        *,
        dense_k: int,
        lexical_k: int,
    ) -> KnowledgeCandidateResult: ...


def build_heldout_policy(
    generation: RetrievalGeneration,
) -> KnowledgeRetrievalPolicy:
    return KnowledgeRetrievalPolicy(
        policy_version="knowledge-heldout-fixed-baseline-v1",
        backend_fingerprint=generation.backend_fingerprint,
        lexical_provider=(
            f"{generation.chinese_tokenizer}+{generation.lexical_ranker}"
        ),
        transformer_version=QUERY_TRANSFORM_PROMPT_VERSION,
        embedding_version=generation.embedding_profile.fingerprint,
        reranker_version="identity-no-rerank-v1",
        packer_version=CONTEXT_PACKER_VERSION,
        raw_query_weight=0.0,
        standalone_query_weight=1.0,
        dense_weight=1.0,
        lexical_weight=0.0,
        rrf_k=RRF_K,
        candidate_k=CANDIDATE_K,
        final_k=FINAL_K,
        context_max_tokens=CONTEXT_MAX_TOKENS,
    )


async def evaluate_postgres_heldout(
    *,
    dataset: RagDataset,
    tenant_id: str,
    locale: str,
    generation: RetrievalGeneration,
    candidate_source: HeldoutCandidateSource,
    transformer: KnowledgeQueryTransformer,
    run_id: str,
    rewrite_concurrency: int = 3,
    expected_case_count: int = 120,
    require_frozen_identity: bool = True,
) -> tuple[dict[str, Any], ...]:
    """Run the one frozen baseline; this function never selects parameters."""
    cases = validate_heldout_dataset(
        dataset,
        expected_case_count=expected_case_count,
        require_frozen_identity=require_frozen_identity,
    )
    if rewrite_concurrency < 1:
        raise ValueError("rewrite_concurrency must be positive")
    policy = build_heldout_policy(generation)
    rewrites = await capture_rewrites(cases, transformer, rewrite_concurrency)
    packer = ContextPacker()
    predictions = []
    for case in cases:
        predictions.append(
            await _evaluate_case(
                case=case,
                rewrite=rewrites[case.case_id],
                tenant_id=tenant_id,
                locale=locale,
                generation=generation,
                source=candidate_source,
                policy=policy,
                packer=packer,
                run_id=run_id,
            )
        )
    return tuple(predictions)


async def _evaluate_case(
    *, case, rewrite, tenant_id, locale, generation, source, policy, packer, run_id
):
    kind = "standalone" if rewrite.status == "REWRITTEN" else "raw"
    query = rewrite.standalone if kind == "standalone" else case.query
    request = KnowledgeRetrievalRequest(
        tenant_id=tenant_id,
        user_scope="public-evaluation",
        authorization_fingerprint="public-evaluation-v1",
        acl_policy_fingerprint="knowledge-public-only-v1",
        deletion_epoch=0,
        requirement_signature="knowledge.heldout_fixed_baseline",
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
    started = time.perf_counter()
    result = await source.capture_source_rankings_async(
        request, [(kind, query, 1.0)], dense_k=SOURCE_K, lexical_k=0
    )
    retrieval_latency_ms = (time.perf_counter() - started) * 1000
    if result.status is not RetrievalStatus.OK:
        empty = evaluate_ranked_hits(case, (), {}, top_k=CANDIDATE_K)
        return build_heldout_prediction(
            run_id,
            case,
            rewrite,
            kind,
            result,
            retrieval_latency_ms,
            source_candidates=(),
            candidate_ids=(),
            candidate_metrics=empty,
            prepack_ids=(),
            prepack_metrics=empty,
            evidence_pack=None,
            packed_metrics=empty,
            token_count=0,
            packing_latency_ms=0.0,
        )

    route = f"{kind}:vector"
    source_candidates = tuple(
        sorted(
            (item for item in result.candidates if route in item.get("ranks", {})),
            key=lambda item: (int(item["ranks"][route]), str(item["chunk_id"])),
        )[:SOURCE_K]
    )
    ranked = source_candidates[:CANDIDATE_K]
    contexts = tuple(
        candidate_to_context(
            item,
            route=route,
            manifest_fingerprint=generation.manifest_hash,
            rrf_k=RRF_K,
        )
        for item in ranked
    )
    hit_by_id = {
        item.chunk_id: {
            "document_id": item.document_id,
            "source_start_char": item.start_char,
            "source_end_char": item.end_char,
        }
        for item in contexts
    }
    candidate_ids = tuple(item.chunk_id for item in contexts)
    candidate_metrics = evaluate_ranked_hits(
        case, candidate_ids, hit_by_id, top_k=CANDIDATE_K
    )
    prepack_ids = candidate_ids[:FINAL_K]
    prepack_metrics = evaluate_ranked_hits(case, prepack_ids, hit_by_id, top_k=FINAL_K)
    started = time.perf_counter()
    packed = packer.pack(
        contexts,
        max_tokens=CONTEXT_MAX_TOKENS,
        max_chunks=FINAL_K,
        redundancy_threshold=1.0,
    )
    packing_latency_ms = (time.perf_counter() - started) * 1000
    evidence_pack = EvidencePack.from_packed(
        case.query,
        packed,
        retrieval_policy=policy.legacy_mapping(),
        retrieval_trace={
            "variants": [{"kind": kind, "query": query, "weight": 1.0}],
            "rewrite_prompt_version": QUERY_TRANSFORM_PROMPT_VERSION,
            "rewrite_error": rewrite.error or "",
            "rerank_prompt_version": "identity-no-rerank-v1",
            "rerank_error": "",
        },
    )
    packed_ids = tuple(item.chunk_id for item in evidence_pack.items)
    packed_metrics = evaluate_ranked_hits(case, packed_ids, hit_by_id, top_k=FINAL_K)
    return build_heldout_prediction(
        run_id,
        case,
        rewrite,
        kind,
        result,
        retrieval_latency_ms,
        source_candidates=source_candidates,
        candidate_ids=candidate_ids,
        candidate_metrics=candidate_metrics,
        prepack_ids=prepack_ids,
        prepack_metrics=prepack_metrics,
        evidence_pack=evidence_pack,
        packed_metrics=packed_metrics,
        token_count=packed.token_count,
        packing_latency_ms=packing_latency_ms,
    )


def validate_heldout_dataset(
    dataset, *, expected_case_count=120, require_frozen_identity=True
):
    cases = dataset.select_cases("heldout")
    if len(cases) != expected_case_count or len(
        {case.group_id for case in cases}
    ) != len(cases):
        raise ValueError("heldout cohort identity/count drift")
    if not cases or not all(case.answerable and case.evidence for case in cases):
        raise ValueError("heldout cohort requires evidence-grounded answerable cases")
    source = dataset.manifest.get("source") or {}
    if require_frozen_identity and (
        dataset.manifest.get("dataset_id") != "doc2dial-rag-en-heldout-balanced-v1"
        or source.get("official_split") != "test"
        or source.get("selection_profile") != "conversation-domain-length-balanced-v1"
        or source.get("corpus_policy") != "all_official_documents"
    ):
        raise ValueError("heldout dataset is not the frozen Doc2Dial cohort")
    return tuple(sorted(cases, key=lambda case: case.case_id))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
