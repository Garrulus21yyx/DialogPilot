from __future__ import annotations

import asyncio
from pathlib import Path

from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    EmbeddingProviderKind,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.knowledge_retriever import KnowledgeCandidateResult
from evaluation.postgres_rag_production_heldout_eval import (
    evaluate_production_heldout,
)
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument
from evaluation.rag_pipeline.dataset import RagDataset
from mcp.source_document import SourceDocument


SHA = "a" * 64


def test_production_eval_uses_expansions_hybrid_candidates_rerank_and_pack():
    dataset = _dataset()
    source = _Source(dataset.documents[0])
    transformer = _Transformer()
    reranker = _Reranker()

    predictions = asyncio.run(evaluate_production_heldout(
        dataset=dataset,
        tenant_id="tenant",
        locale="en",
        generation=_generation(),
        candidate_source=source,
        transformer=transformer,
        reranker=reranker,
        run_id="run",
        expected_case_count=1,
    ))

    row = predictions[0]
    assert [item["kind"] for item in row["variants"]] == [
        "raw", "standalone", "expansion-1", "expansion-2",
    ]
    assert source.calls[0][1] == 20
    assert row["candidate_metrics"]["evidence_recall"] == 1.0
    assert row["prepack_metrics"]["evidence_recall"] == 1.0
    assert row["packed_metrics"]["evidence_recall"] == 1.0
    assert row["rerank_fallback"] is False


class _Transformer:
    async def standalone(self, query, history):
        assert history
        return "resolved refund question", None

    async def expand(self, query, *, n):
        assert n == 2
        return ("refund timing", "refund policy"), None


class _Reranker:
    async def rerank(self, query, candidates):
        return tuple(item["chunk_id"] for item in reversed(candidates)), False


class _Source:
    def __init__(self, document):
        self.document = document
        self.calls = []

    async def search_variants_async(self, request, variants, *, top_k):
        self.calls.append((variants, top_k))
        source = SourceDocument.create(
            source_id=self.document.document_id,
            title=self.document.title,
            content=self.document.content,
            source_type="text",
        )
        return KnowledgeCandidateResult(RetrievalStatus.OK, ({
            "chunk_id": "chunk-one",
            "source_id": source.source_id,
            "source_revision": source.revision_id,
            "source_checksum": source.checksum,
            "source_start_char": 0,
            "source_end_char": len(source.content),
            "content": source.content,
            "title": source.title,
            "source_type": source.source_type,
            "scope": "public",
            "scope_decision": "allowed_public",
            "index_manifest_fingerprint": request.manifest_fingerprint,
            "ranks": {"raw:vector": 1, "raw:bm25": 1},
            "score": 1.0,
        },))


def _dataset():
    document = RagDocument("doc", "Refund", "refund evidence")
    case = RagCase(
        case_id="case", group_id="group", split="heldout",
        query="what about it?", history=("refund", "prior answer"),
        evidence=(EvidenceSpan("doc", 0, len(document.content), document.content),),
        query_types=("customer_support", "multi_turn", "dmv", "short_document"),
    )
    return RagDataset(Path("."), {
        "dataset_id": "doc2dial-rag-en-heldout-fixture",
        "corpus_sha256": "b" * 64,
        "cases_sha256": "c" * 64,
        "source": {
            "official_split": "test",
            "corpus_policy": "all_official_documents",
        },
    }, (document,), (case,))


def _generation():
    profile = EmbeddingProfile(
        provider="fixture", provider_kind=EmbeddingProviderKind.MODEL,
        model="fixture", model_version="v1", dimension=3,
        model_digest="d" * 64, document_preprocessing="doc-v1",
        query_preprocessing="query-v1",
    )
    return RetrievalGeneration(
        generation_id="generation", corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id="POSTGRES_PG_BM25_ZH_V1", backend_fingerprint="backend",
        schema_version="retrieval-v1", source_watermark="watermark",
        embedding_model=profile.model, embedding_dimension=profile.dimension,
        embedding_model_digest=profile.model_digest,
        distance_metric=DistanceMetric.COSINE, vector_extension_version="0.8.6",
        index_method="HNSW", index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_BM25_ZH_V1", manifest_hash=SHA,
        state=GenerationState.ACTIVE, embedding_provider=profile.provider,
        embedding_provider_kind=profile.provider_kind,
        embedding_model_version=profile.model_version,
        embedding_document_preprocessing=profile.document_preprocessing,
        embedding_query_preprocessing=profile.query_preprocessing,
    )
