from __future__ import annotations

import asyncio
import json
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
from core.model_policy import ModelPolicy
from evaluation.postgres_rag_heldout_eval import (
    build_heldout_policy,
    evaluate_postgres_heldout,
)
from evaluation.rag_heldout_artifacts import write_heldout_artifacts
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument
from evaluation.rag_pipeline.dataset import RagDataset
from mcp.source_document import SourceDocument
from scripts.run_postgres_rag_heldout_eval import ingest_documents


def test_fixed_heldout_chain_rewrites_retrieves_packs_and_writes_artifacts(tmp_path):
    dataset = _dataset()
    generation = _generation()
    transformer = _Transformer()
    source = _Source(dataset.documents)

    predictions = asyncio.run(
        evaluate_postgres_heldout(
            dataset=dataset,
            tenant_id="heldout-tenant",
            locale="en",
            generation=generation,
            candidate_source=source,
            transformer=transformer,
            run_id="heldout-run",
            expected_case_count=2,
            require_frozen_identity=False,
        )
    )

    assert transformer.calls == ["case one?", "case two"]
    assert source.calls == [
        ("standalone", 40, 0),
        ("raw", 40, 0),
    ]
    assert all(
        item["candidate_metrics"]["evidence_recall"] == 1.0 for item in predictions
    )
    assert all(item["packed_metrics"]["evidence_recall"] == 1.0 for item in predictions)
    assert all(item["evidence_pack"]["items"] for item in predictions)
    assert not any(
        "text" in evidence
        for item in predictions
        for evidence in item["evidence_pack"]["items"]
    )

    output = tmp_path / "output"
    policy = ModelPolicy.from_env(
        {
            "MODEL_PROVIDER": "deepseek",
            "MODEL_REWRITE": "deepseek-v4-flash",
            "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
        }
    )
    report = write_heldout_artifacts(
        output_dir=output,
        dataset=dataset,
        predictions=predictions,
        run_id="heldout-run",
        generation=generation,
        policy=build_heldout_policy(generation),
        model_policy=policy,
        chunk_count=2,
        index_prepare_latency_ms=1.0,
    )

    assert set(item.name for item in output.iterdir()) == {
        "manifest.json",
        "predictions.jsonl",
        "report.json",
    }
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["evaluation_role"] == "HELDOUT_FIXED_BASELINE"
    assert manifest["configuration_selection_allowed"] is False
    assert manifest["fixed_config"]["source_k"] == 40
    assert manifest["fixed_config"]["final_k"] == 5
    assert manifest["fixed_config"]["context_max_tokens"] == 2600
    assert "parent_expansion" in manifest["stages_not_run"]
    assert report["candidate"]["all_evidence_recall"]["rate"] == 1.0
    assert report["packed"]["all_evidence_recall"]["rate"] == 1.0


def test_full_corpus_uses_the_store_incremental_batch_contract():
    class Store:
        def __init__(self):
            self.batches = []

        def add_documents(self, documents):
            self.batches.append(tuple(documents))
            return sum(len(batch) for batch in self.batches)

    store = Store()
    count = ingest_documents(store, tuple(range(488)))

    assert tuple(map(len, store.batches)) == (256, 232)
    assert count == 488


class _Transformer:
    def __init__(self):
        self.calls = []

    async def standalone(self, query, history):
        self.calls.append(query)
        assert history
        return ("resolved case one", None) if query == "case one?" else (query, None)


class _Source:
    def __init__(self, documents):
        self.documents = {item.document_id: item for item in documents}
        self.calls = []

    async def capture_source_rankings_async(
        self, request, variants, *, dense_k, lexical_k
    ):
        kind, query, weight = variants[0]
        assert weight == 1.0
        self.calls.append((kind, dense_k, lexical_k))
        document_id = "one" if "one" in query else "two"
        document = self.documents[document_id]
        source = SourceDocument.create(
            source_id=document_id,
            title=document_id,
            content=document.content,
            source_type="text",
        )
        route = f"{kind}:vector"
        return KnowledgeCandidateResult(
            RetrievalStatus.OK,
            (
                {
                    "chunk_id": f"{document_id}-chunk",
                    "source_id": document_id,
                    "source_revision": source.revision_id,
                    "source_checksum": source.checksum,
                    "source_start_char": 0,
                    "source_end_char": len(document.content),
                    "content": document.content,
                    "title": document_id,
                    "source_type": "text",
                    "scope": "public",
                    "scope_decision": "allowed_public",
                    "ranks": {route: 1},
                },
            ),
        )


def _dataset():
    documents = (
        RagDocument("one", "One", "first evidence"),
        RagDocument("two", "Two", "second evidence"),
    )
    cases = tuple(
        RagCase(
            case_id=f"case-{index}",
            group_id=f"group-{index}",
            split="heldout",
            query=query,
            history=("prior question", "prior answer"),
            evidence=(
                EvidenceSpan(
                    document.document_id, 0, len(document.content), document.content
                ),
            ),
            query_types=("customer_support", "multi_turn", "dmv", "short_document"),
        )
        for index, (query, document) in enumerate(
            zip(("case one?", "case two"), documents, strict=True), 1
        )
    )
    return RagDataset(
        Path("."),
        {
            "dataset_id": "fixture-heldout",
            "corpus_sha256": "c" * 64,
            "cases_sha256": "d" * 64,
            "source": {"fixture": True},
        },
        documents,
        cases,
    )


def _generation():
    profile = EmbeddingProfile(
        provider="fixture-bge",
        provider_kind=EmbeddingProviderKind.MODEL,
        model="BAAI/bge-m3",
        model_version="fixture-revision",
        dimension=1024,
        model_digest="e" * 64,
        document_preprocessing="raw-v1",
        query_preprocessing="raw-v1",
    )
    return RetrievalGeneration(
        generation_id="heldout-generation",
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint="POSTGRES_PGVECTOR_PG_FTS_ZH_V1",
        schema_version="retrieval-v1",
        source_watermark="source-watermark",
        embedding_model=profile.model,
        embedding_dimension=profile.dimension,
        embedding_model_digest=profile.model_digest,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer="jieba-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash="f" * 64,
        state=GenerationState.ACTIVE,
        embedding_provider=profile.provider,
        embedding_provider_kind=profile.provider_kind,
        embedding_model_version=profile.model_version,
        embedding_document_preprocessing=profile.document_preprocessing,
        embedding_query_preprocessing=profile.query_preprocessing,
    )
