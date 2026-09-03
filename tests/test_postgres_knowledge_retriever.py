"""PostgreSQL is the authoritative online Knowledge candidate source."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from application.chinese_lexical import TOKENIZER_VERSION, postgres_lexical_document
from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    EmbeddingProviderKind,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.knowledge_retriever import (
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
)
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_retriever import (
    PostgresKnowledgeCandidateSource,
    PostgresKnowledgeEvidenceValidator,
)
from mcp.evidence_pack import EvidenceItem, EvidencePack, SourceReference
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)


MANIFEST = "a" * 64
CONTENT = "退款审核完成后，款项将在三个工作日内原路退回。"
CHECKSUM = hashlib.sha256(CONTENT.encode()).hexdigest()
EMBEDDING_PROFILE = EmbeddingProfile(
    provider="test-model-provider",
    provider_kind=EmbeddingProviderKind.MODEL,
    model="all-MiniLM-L6-v2",
    model_version="test-revision-1",
    dimension=384,
    model_digest="b" * 64,
    document_preprocessing="raw-document-test-v1",
    query_preprocessing="raw-query-test-v1",
)


def _generation() -> RetrievalGeneration:
    return RetrievalGeneration(
        generation_id="knowledge-online-generation",
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint="postgres-knowledge-v1",
        schema_version="retrieval-v1",
        source_watermark="source-revision-one",
        embedding_model="all-MiniLM-L6-v2",
        embedding_dimension=384,
        embedding_model_digest="b" * 64,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer=TOKENIZER_VERSION,
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=MANIFEST,
        embedding_provider=EMBEDDING_PROFILE.provider,
        embedding_provider_kind=EMBEDDING_PROFILE.provider_kind,
        embedding_model_version=EMBEDDING_PROFILE.model_version,
        embedding_document_preprocessing=(
            EMBEDDING_PROFILE.document_preprocessing
        ),
        embedding_query_preprocessing=EMBEDDING_PROFILE.query_preprocessing,
    )


def _policy() -> KnowledgeRetrievalPolicy:
    return KnowledgeRetrievalPolicy(
        policy_version="knowledge-policy-v1",
        backend_fingerprint="postgres-knowledge-v1",
        lexical_provider="PG_FTS_ZH_V1",
        transformer_version="standalone-v1",
        embedding_version=EMBEDDING_PROFILE.fingerprint,
        reranker_version="reranker-v1",
        packer_version="context-packer-v1",
    )


def _request() -> KnowledgeRetrievalRequest:
    return KnowledgeRetrievalRequest(
        tenant_id="tenant-a", user_scope="user-a",
        authorization_fingerprint="auth-a", acl_policy_fingerprint="public-v1",
        deletion_epoch=0, requirement_signature="knowledge.active_source",
        query="退款多久到账", history=(), conversation_range_hash="range-a",
        locale="zh-CN", product=None, manifest_fingerprint=MANIFEST,
        generation_id="knowledge-online-generation", policy=_policy(),
    )


@pytest.fixture()
def knowledge_source(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
        statement_timeout_ms=2000,
    ))
    platform.open()
    retrieval.open()
    with platform.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                retrieval.knowledge_chunk_search,
                retrieval.knowledge_source_manifests,
                retrieval.knowledge_source_revisions,
                retrieval.retrieval_generation_registry
            CASCADE
        """)
    registry = PostgresRetrievalGenerationRegistry(platform)
    generation = registry.register(_generation())
    registry.transition(generation.generation_id, GenerationState.BUILDING)
    embedding = "[" + ",".join(["1"] + ["0"] * 383) + "]"
    with platform.transaction() as connection:
        connection.execute("""
            INSERT INTO retrieval.knowledge_source_revisions (
                tenant_id, source_id, revision_id, checksum, title,
                source_type, content, effective_from, immutable_fingerprint,
                owner_id, scope, locale, product, region,
                operations_audit_ref, schema_version
            ) VALUES (
                'tenant-a', 'refund-policy', 'revision-one', %s,
                '退款政策', 'text', %s, now(), %s,
                'local-owner', 'public', 'zh-CN', '', 'local',
                'local-direct-ingest', 'knowledge-source-v0'
            )
        """, (CHECKSUM, CONTENT, "c" * 64))
        connection.execute("""
            INSERT INTO retrieval.knowledge_source_manifests (
                tenant_id, backend_id, generation_id, scope, locale,
                product, manifest_hash, source_count, schema_version,
                reviewer_manifest_ref
            ) VALUES (
                'tenant-a', %s, %s, 'public', 'zh-CN', '', %s, 1,
                'knowledge-source-v0', 'local-direct-ingest'
            )
        """, (generation.backend_id, generation.generation_id, MANIFEST))
        connection.execute("""
            INSERT INTO retrieval.knowledge_chunk_search (
                candidate_id, tenant_id, backend_id, generation_id,
                source_id, source_revision, source_checksum, source_span,
                provenance_sha256, scope, locale, product, deletion_epoch,
                embedding, lexical_document, projected_at
            ) VALUES (
                'chunk-refund', 'tenant-a', %s, %s,
                'refund-policy', 'revision-one', %s,
                jsonb_build_object('start_char', 0, 'end_char', %s),
                %s, 'public', 'zh-CN', NULL, 0, %s::vector, %s, now()
            )
        """, (
            generation.backend_id, generation.generation_id, CHECKSUM,
            len(CONTENT), "d" * 64, embedding,
            postgres_lexical_document(CONTENT),
        ))
    registry.transition(generation.generation_id, GenerationState.READY)
    registry.activate_direct(generation.generation_id)
    source = PostgresKnowledgeCandidateSource(
        backend=PostgresHybridBackend(retrieval), generations=registry,
        pool=retrieval,
        embed_query=lambda _query, _generation: tuple([1.0] + [0.0] * 383),
    )
    try:
        yield source
    finally:
        retrieval.close()
        platform.close()


def test_source_fuses_pg_routes_and_resolves_canonical_source(knowledge_source):
    result = asyncio.run(knowledge_source.search_variants_async(
        _request(), [("raw", "退款多久到账", 1.0)], top_k=20,
    ))

    assert result.status is RetrievalStatus.OK
    assert result.candidates == ({
        "chunk_id": "chunk-refund", "source_id": "refund-policy",
        "source_revision": "revision-one", "source_checksum": CHECKSUM,
        "source_start_char": 0, "source_end_char": len(CONTENT),
        "content": CONTENT, "title": "退款政策",
        "source_type": "text", "scope": "public",
        "index_manifest_fingerprint": MANIFEST,
        "ranks": {"raw:vector": 1, "raw:lexical": 1},
        "score": pytest.approx(1 / 11),
        "scope_decision": "allowed_public",
    },)
    assert knowledge_source.validate_candidates(result.candidates, _request())


def test_source_rejects_manifest_drift_without_partial_candidates(knowledge_source):
    request = _request()
    request = KnowledgeRetrievalRequest(**{
        **request.__dict__, "manifest_fingerprint": "e" * 64,
    })
    result = asyncio.run(knowledge_source.search_variants_async(
        request, [("raw", request.query, 1.0)], top_k=20,
    ))
    assert result.status is RetrievalStatus.CONFLICT
    assert result.detail_code == "MANIFEST_FINGERPRINT_DRIFT"
    assert result.candidates == ()


def test_source_rejects_policy_embedding_profile_drift(knowledge_source):
    request = _request()
    request = KnowledgeRetrievalRequest(**{
        **request.__dict__,
        "policy": KnowledgeRetrievalPolicy(**{
            **request.policy.__dict__, "embedding_version": "different-profile",
        }),
    })
    result = asyncio.run(knowledge_source.search_variants_async(
        request, [("raw", request.query, 1.0)], top_k=20,
    ))

    assert result.status is RetrievalStatus.CONFLICT
    assert result.detail_code == "EMBEDDING_PROFILE_FINGERPRINT_DRIFT"
    assert result.candidates == ()


def test_evidence_validator_reads_source_type_from_provenance_owner():
    class CapturingSource:
        def validate_candidates(self, candidates, _request):
            assert candidates[0]["source_type"] == "text"
            return True

    pack = EvidencePack(
        query="退款多久到账", index_manifest_fingerprint=MANIFEST,
        retrieval_policy=(),
        items=(EvidenceItem(
            chunk_id="chunk-refund", title="退款政策",
            source_ref=SourceReference(
                source_id="refund-policy", source_revision="revision-one",
                start_char=0, end_char=len(CONTENT), source_type="text",
                checksum=CHECKSUM,
            ),
            score=1.0, rank=1, source_ranks=(("raw:vector", 1),),
            scope_decision="allowed_public", text=CONTENT,
        ),),
    )

    assert PostgresKnowledgeEvidenceValidator(CapturingSource()).validate(
        pack, _request(),
    )
