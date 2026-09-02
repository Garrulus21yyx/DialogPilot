#!/usr/bin/env python3
"""Build a canonical PG shadow generation and emit the frozen M2-T05 report."""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

from application.chinese_lexical import TOKENIZER_VERSION
from application.data_location_registry import (
    DataLocationRegistry,
    DataSubjectRef,
    DataWriteIntent,
    DurableWriteKind,
    default_registry_path,
)
from application.hybrid_retrieval import (
    DistanceMetric,
    GenerationConflict,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from application.knowledge_source import (
    KnowledgeChunkProjection,
    KnowledgeSourceManifest,
    SourceRevision,
)
from evaluation.knowledge_pg_shadow import (
    FrozenKnowledgeShadow,
    canonical_hash,
    read_jsonl,
    run_shadow,
    write_immutable_report,
)
from infrastructure.hybrid_retrieval_backend import (
    ChromaBm25KnowledgeCandidateSource,
    LegacyHybridBackend,
    PostgresHybridBackend,
)
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_knowledge_source import PostgresKnowledgeSourceRepository
from infrastructure.postgres_retrieval_projection import PostgresCanonicalRetrievalProjector
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)
from mcp.sparse_index import PersistentBM25Index, SparseDocument


def _source_revisions(spec: FrozenKnowledgeShadow) -> tuple[SourceRevision, ...]:
    effective = datetime.fromisoformat(str(spec.raw["frozen_at"]))
    tenant = str(spec.raw["filters"]["tenant_id"])
    return tuple(SourceRevision.create(
        tenant_id=tenant, source_id=str(row["source_id"]),
        title=str(row["title"]), source_type=str(row["source_type"]),
        content=str(row["content"]), effective_from=effective,
    ) for row in read_jsonl(spec.corpus_path))


def _provenance(generation_id: str, source: SourceRevision) -> str:
    return hashlib.sha256(
        f"{generation_id}\0{source.source_id}\0{source.revision_id}\00\0"
        f"{len(source.content)}".encode("utf-8")
    ).hexdigest()


def _generation(
    spec: FrozenKnowledgeShadow, manifest: KnowledgeSourceManifest,
) -> RetrievalGeneration:
    identity = spec.raw["postgres_generation"]
    return RetrievalGeneration(
        generation_id=str(identity["generation_id"]),
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id=str(identity["backend_id"]),
        backend_fingerprint=str(identity["backend_fingerprint"]),
        schema_version="retrieval-v1", source_watermark=manifest.manifest_hash,
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension=384,
        embedding_model_digest="UNVERIFIED:legacy-model-weight-digest",
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6", index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer=TOKENIZER_VERSION, lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=manifest.manifest_hash,
    )


def _legacy_generation(spec: FrozenKnowledgeShadow) -> RetrievalGeneration:
    identity = spec.raw["legacy_generation"]
    return RetrievalGeneration(
        generation_id=str(identity["generation_id"]),
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id=str(identity["backend_id"]),
        backend_fingerprint=str(identity["backend_fingerprint"]),
        schema_version="knowledge-index-v3",
        source_watermark=str(spec.raw["corpus"]["sha256"]),
        embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        embedding_dimension=384,
        embedding_model_digest="UNVERIFIED:legacy-model-weight-digest",
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="chroma-0.5.23", index_method="HNSW",
        index_params_json='{"engine":"chroma-default"}',
        chinese_tokenizer=TOKENIZER_VERSION, lexical_ranker="LEGACY_BM25_V1",
        manifest_hash=str(spec.raw["corpus"]["sha256"]),
        state=GenerationState.READY,
    )


def execute(spec_path: Path, database_url: str, output: Path) -> dict:
    spec = FrozenKnowledgeShadow.load(spec_path)
    registry = DataLocationRegistry.load(default_registry_path())
    registry.authorize_contract(DataWriteIntent(
        location_id="location:knowledge-index:v1", producer="knowledge-shadow",
        kind=DurableWriteKind.DARK_SHADOW, schema_version="retrieval-v1",
        retention_class="knowledge_governed",
        subject=DataSubjectRef(str(spec.raw["filters"]["tenant_id"]), "", ""),
        expected_deletion_epoch=0,
    ))
    sources = _source_revisions(spec)
    embedder = DefaultEmbeddingFunction()
    embeddings = embedder([item.content for item in sources])
    candidate_prefix = str(spec.raw["shadow_id"])
    candidates = tuple(
        KnowledgeChunkProjection(
            candidate_id=f"{candidate_prefix}:{source.source_id}:0",
            source_id=source.source_id, revision_id=source.revision_id,
            source_checksum=source.checksum, start_char=0,
            end_char=len(source.content), lexical_document=source.content,
            provenance_sha256=_provenance(
                str(spec.raw["postgres_generation"]["generation_id"]), source,
            ),
            embedding=tuple(map(float, embedding)),
        )
        for source, embedding in zip(sources, embeddings, strict=True)
    )
    manifest = KnowledgeSourceManifest.build(
        tenant_id=str(spec.raw["filters"]["tenant_id"]),
        backend_id=str(spec.raw["postgres_generation"]["backend_id"]),
        generation_id=str(spec.raw["postgres_generation"]["generation_id"]),
        scope=str(spec.raw["filters"]["scope"]),
        locale=str(spec.raw["filters"]["locale"]),
        product=str(spec.raw["filters"].get("product") or ""), sources=sources,
        reviewer_manifest_ref="data/eval/knowledge-source-v0/manifest.json",
    )

    PostgresMigrationRunner(database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(database_url, min_size=1, max_size=2))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        database_url, min_size=1, max_size=2, statement_timeout_ms=2000,
    ))
    platform.open()
    retrieval.open()
    sparse = PersistentBM25Index(":memory:")
    try:
        generations = PostgresRetrievalGenerationRegistry(platform)
        expected_generation = _generation(spec, manifest)
        try:
            current_generation = generations.get(expected_generation.generation_id)
        except GenerationConflict:
            generation = generations.register(expected_generation)
        else:
            generation = generations.register(replace(
                expected_generation, state=current_generation.state,
            ))
        if generation.state is GenerationState.REGISTERED:
            generation = generations.transition(generation.generation_id, GenerationState.BUILDING)
        if generation.state is GenerationState.BUILDING:
            PostgresKnowledgeSourceRepository(platform).backfill_generation(
                manifest, sources, candidates,
            )
            with platform.transaction() as connection:
                event_id = connection.execute("""
                    SELECT event_id FROM retrieval.canonical_projection_outbox
                    WHERE generation_id=%s AND source_ref=%s
                """, (generation.generation_id, manifest.manifest_hash)).fetchone()[0]
            projection = PostgresCanonicalRetrievalProjector(platform).project(str(event_id))
            if projection.code.value not in {"APPLIED", "ALREADY_APPLIED"}:
                raise RuntimeError(f"canonical shadow projection failed: {projection.code.value}")
            generation = generations.transition(generation.generation_id, GenerationState.READY)
        if generation.state is not GenerationState.READY:
            raise RuntimeError("dark-shadow generation must remain READY and non-active")

        client = chromadb.EphemeralClient()
        collection = client.create_collection(
            "m2_t05_shadow_legacy", embedding_function=None,
        )
        metadatas = [{
            "source_id": source.source_id, "source_revision": source.revision_id,
            "source_checksum": source.checksum,
            "provenance_sha256": candidate.provenance_sha256,
            "scope": str(spec.raw["filters"]["scope"]),
            "locale": str(spec.raw["filters"]["locale"]),
        } for source, candidate in zip(sources, candidates, strict=True)]
        collection.add(
            ids=[item.candidate_id for item in candidates],
            documents=[item.content for item in sources], metadatas=metadatas,
            embeddings=[list(map(float, item)) for item in embeddings],
        )
        sparse.ensure(tuple(
            SparseDocument(item.candidate_id, source.content)
            for item, source in zip(candidates, sources, strict=True)
        ), corpus_fingerprint=str(spec.raw["corpus"]["sha256"]))
        legacy_generation = _legacy_generation(spec)
        legacy = LegacyHybridBackend(
            generations={legacy_generation.generation_id: legacy_generation},
            sources={RetrievalCorpus.KNOWLEDGE: ChromaBm25KnowledgeCandidateSource(
                collection=collection, sparse_index=sparse,
                tenant_id=str(spec.raw["filters"]["tenant_id"]),
            )},
        )
        report = run_shadow(
            spec, legacy_backend=legacy,
            postgres_backend=PostgresHybridBackend(retrieval),
            embed_query=lambda text: embedder([text])[0],
        )
        report["data_location_registry"] = {
            **registry.artifact_summary(),
            "location_id": "location:knowledge-index:v1",
            "producer": "knowledge-shadow",
        }
        base = {key: value for key, value in report.items() if key != "report_sha256"}
        report["report_sha256"] = canonical_hash(base)
        write_immutable_report(output, report)
        return report
    finally:
        sparse.close()
        retrieval.close()
        platform.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = execute(args.spec, args.database_url, args.output)
    print(json.dumps({
        "shadow_id": report["shadow_id"], "metrics": report["metrics"],
        "decision": report["decision"], "report_sha256": report["report_sha256"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
