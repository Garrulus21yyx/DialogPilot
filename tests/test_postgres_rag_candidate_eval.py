from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

import pytest

from evaluation.postgres_rag_candidate_eval import (
    CHUNK_PROFILES,
    evaluate_postgres_candidates,
)
from evaluation.rag_pipeline.dataset import RagDataset, write_dataset
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.knowledge_embedding import LocalHashKnowledgeEmbeddingBaseline
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)
from mcp.source_document import SourceDocument
from scripts.run_postgres_rag_eval import preflight_environment


def test_real_postgres_candidate_eval_is_raw_only_and_writes_artifacts(
    fresh_postgres_database_url, tmp_path,
):
    content = "Refunds arrive within five business days after approval."
    dataset_root = tmp_path / "dataset"
    write_dataset(
        dataset_root,
        dataset_id="candidate-eval-test-v1",
        documents=[
            {"id": "refund", "title": "Refund timing", "content": content},
            {
                "id": "shipping",
                "title": "Shipping timing",
                "content": "Standard shipping arrives within ten business days.",
            },
        ],
        cases=[{
            "id": "refund-1",
            "group_id": "refund-group",
            "split": "dev",
            "query": "refunds five business days",
            "history": ["This history must not enter the raw-only request."],
            "evidence": [{
                "document_id": "refund",
                "start_char": 0,
                "end_char": len(content),
                "quote": content,
            }],
            "answerable": True,
        }],
        source={"kind": "test"},
    )
    dataset = RagDataset.load(dataset_root)
    PostgresMigrationRunner(fresh_postgres_database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(
        fresh_postgres_database_url, min_size=1, max_size=2,
    ))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        fresh_postgres_database_url, min_size=1, max_size=2,
        statement_timeout_ms=5_000,
    ))
    platform.open()
    retrieval.open()
    try:
        provider = LocalHashKnowledgeEmbeddingBaseline()
        store = PostgresKnowledgeStore(
            platform,
            tenant_id="candidate-eval-test",
            locale="en",
            chunk_strategy="structure_aware",
            chunk_max_tokens=256,
            chunk_overlap_tokens=32,
            embedding_provider=provider,
        )
        chunk_count = store.add_documents(tuple(
            SourceDocument.create(
                source_id=item.document_id,
                title=item.title,
                content=item.content,
            )
            for item in dataset.documents
        ))
        generation = store.active_generation()
        source = PostgresKnowledgeCandidateSource(
            backend=PostgresHybridBackend(retrieval),
            generations=PostgresRetrievalGenerationRegistry(platform),
            pool=retrieval,
            embed_query=store.embed_query,
        )
        output = tmp_path / "result"
        report = asyncio.run(evaluate_postgres_candidates(
            dataset=dataset,
            split="dev",
            tenant_id="candidate-eval-test",
            locale="en",
            generation=generation,
            candidate_source=source,
            chunk_profile_id="structure-256-32",
            chunk_count=chunk_count,
            index_prepare_latency_ms=1.0,
            output_dir=output,
            run_id="candidate-eval-run",
        ))
    finally:
        retrieval.close()
        platform.close()

    assert {path.name for path in output.iterdir()} == {
        "manifest.json", "predictions.jsonl", "report.json",
    }
    manifest = json.loads((output / "manifest.json").read_text())
    prediction = json.loads((output / "predictions.jsonl").read_text())
    assert manifest["evaluation_scope"] == "knowledge_candidate_only"
    assert manifest["stages_executed"] == ["raw_query", "candidate_retrieval"]
    assert manifest["query"] == {"history_consumed": False, "mode": "raw_only"}
    assert manifest["dataset"]["corpus_sha256"] == dataset.manifest["corpus_sha256"]
    assert manifest["embedding_profile"]["fingerprint"] == provider.profile.fingerprint
    assert report["run_status"] == "COMPLETED"
    assert report["candidate_metrics"]["evidence_recall"] == 1.0
    assert report["all_evidence_recall"] == {"passed": 1, "total": 1, "rate": 1.0}
    assert prediction["query_mode"] == "raw_only"
    assert prediction["candidates"][0]["document_id"] == "refund"


def test_candidate_cli_preflight_is_explicit_and_profiles_are_bounded(tmp_path):
    model_dir = tmp_path / "bge-m3"
    model_dir.mkdir()
    values = {
        "EVAL_DATABASE_URL": "postgresql://eval@db:5432/dialogpilot_eval",
        "DATABASE_URL": "postgresql://prod@db:5432/dialogpilot",
        "RETRIEVAL_DATABASE_URL": "postgresql://prod@db:5432/dialogpilot",
        "KNOWLEDGE_DENSE_EMBEDDING_PROVIDER": "bge_m3",
        "BGE_M3_LOCAL_MODEL_PATH": str(model_dir),
        "BGE_M3_MODEL_REVISION": "pinned-revision",
        "BGE_M3_MODEL_SHA256": "a" * 64,
    }
    assert preflight_environment(values) == values["EVAL_DATABASE_URL"]
    assert {name: asdict(profile) for name, profile in CHUNK_PROFILES.items()} == {
        "structure-256-32": {
            "strategy": "structure_aware", "max_tokens": 256, "overlap_tokens": 32,
        },
        "structure-384-48": {
            "strategy": "structure_aware", "max_tokens": 384, "overlap_tokens": 48,
        },
        "structure-512-64": {
            "strategy": "structure_aware", "max_tokens": 512, "overlap_tokens": 64,
        },
        "fixed-512-64": {
            "strategy": "fixed_tokens", "max_tokens": 512, "overlap_tokens": 64,
        },
    }
    with pytest.raises(ValueError, match="explicitly set to bge_m3"):
        preflight_environment({**values, "KNOWLEDGE_DENSE_EMBEDDING_PROVIDER": "hash_baseline"})
    with pytest.raises(ValueError, match="same database as DATABASE_URL"):
        preflight_environment({
            **values,
            "EVAL_DATABASE_URL": "postgresql://eval@db:5432/dialogpilot",
        })
