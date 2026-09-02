"""M2-T04A immutable SourceRevision, backfill, and active-manifest tests."""
from datetime import datetime, timezone
import hashlib

import psycopg
import pytest

from application.authority_policy import AuthorityPolicyRegistry
from application.coverage_gate import RequirementCoverageGate
from application.evidence_receipt import (
    EvidenceReceiptIssuer,
    KnowledgeLocator,
    RequirementStatus,
)
from application.hybrid_retrieval import (
    DistanceMetric,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.knowledge_source import (
    KnowledgeChunkProjection,
    KnowledgeSourceContractError,
    KnowledgeSourceManifest,
    SourceRevision,
)
from application.chinese_lexical import postgres_lexical_document
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_source import PostgresKnowledgeSourceRepository
from infrastructure.postgres_retrieval_projection import (
    PostgresCanonicalRetrievalProjector,
)
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def source_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=3,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                retrieval.knowledge_request_manifest_pins,
                retrieval.knowledge_publication_audit,
                retrieval.knowledge_publication_pointers,
                retrieval.knowledge_candidates,
                retrieval.knowledge_source_revision_audit,
                retrieval.knowledge_source_revision_lifecycle,
                retrieval.canonical_projection_receipts,
                retrieval.canonical_projection_outbox,
                retrieval.knowledge_source_chunk_specs,
                retrieval.knowledge_source_manifest_entries,
                retrieval.knowledge_source_manifests,
                retrieval.knowledge_source_revisions,
                retrieval.knowledge_chunk_search,
                retrieval.service_episode_search,
                retrieval.retrieval_generation_pointers,
                retrieval.retrieval_generation_registry
            CASCADE
        """)
    try:
        yield pool
    finally:
        pool.close()


def _source(content="退款通常在审核通过后按原支付渠道退回。", suffix="one"):
    return SourceRevision.create(
        tenant_id="tenant-knowledge", source_id=f"source-{suffix}",
        title="退款政策", source_type="text", content=content,
        effective_from=NOW,
    )


def _manifest(source, generation_id):
    return KnowledgeSourceManifest.build(
        tenant_id=source.tenant_id,
        backend_id="pg-knowledge-v1",
        generation_id=generation_id,
        scope="public",
        locale="zh-CN",
        product="",
        sources=(source,),
        reviewer_manifest_ref=(
            "data/eval/knowledge-source-v0/manifest.json"
        ),
    )


def _generation(manifest):
    return RetrievalGeneration(
        generation_id=manifest.generation_id,
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id=manifest.backend_id,
        backend_fingerprint="pg-knowledge-backend-v1",
        schema_version="retrieval-v1",
        source_watermark=manifest.manifest_hash,
        embedding_model="test-embedding-v1",
        embedding_dimension=3,
        embedding_model_digest="d" * 64,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.1",
        index_method="EXACT",
        index_params_json="{}",
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=manifest.manifest_hash,
    )


def _chunk(source, generation_id):
    provenance = hashlib.sha256(
        f"{generation_id}\0{source.source_id}\0{source.revision_id}\0"
        f"0\0{len(source.content)}".encode("utf-8")
    ).hexdigest()
    return KnowledgeChunkProjection(
        candidate_id=f"{generation_id}:{source.source_id}:0",
        source_id=source.source_id,
        revision_id=source.revision_id,
        source_checksum=source.checksum,
        start_char=0,
        end_char=len(source.content),
        lexical_document=postgres_lexical_document(source.content),
        provenance_sha256=provenance,
        embedding=(0.1, 0.2, 0.3),
    )


def _build(pool, source, generation_id):
    manifest = _manifest(source, generation_id)
    generations = PostgresRetrievalGenerationRegistry(pool)
    generations.register(_generation(manifest))
    generations.transition(generation_id, GenerationState.BUILDING)
    repository = PostgresKnowledgeSourceRepository(pool)
    repository.write_generation(
        manifest, (source,), (_chunk(source, generation_id),)
    )
    result = PostgresCanonicalRetrievalProjector(pool).project_next()
    assert result is not None
    assert result.code.value == "APPLIED"
    return manifest, generations, repository


def _locator(source, generation_id):
    return KnowledgeLocator(
        source.tenant_id, "pg-knowledge-v1", generation_id,
        "public", "zh-CN", None, source.source_id, source.revision_id,
        source.checksum, 0, len(source.content),
    )


def _receipt(repository, source, generation_id):
    locator = _locator(source, generation_id)
    return EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1()).adapter(
        "knowledge-evidence-adapter", "knowledge-evidence-adapter-v1"
    ).issue(
        requirement_id="knowledge.active_source",
        producer_id="knowledge_search",
        producer_version="knowledge-evidence-pack-result-v1",
        locator=locator,
        status=RetrievalStatus.OK,
        observed_at=NOW,
        payload=repository.resolve(locator),
    )


def test_backfill_is_idempotent_and_source_revision_is_dereferenceable(source_pool):
    source = _source()
    manifest, generations, repository = _build(
        source_pool, source, "knowledge-generation-1"
    )
    repository.write_generation(
        manifest, (source,), (_chunk(source, manifest.generation_id),)
    )
    with source_pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(*) FROM retrieval.canonical_projection_outbox
            WHERE generation_id=%s
        """, (manifest.generation_id,)).fetchone()[0] == 1
    generations.transition(manifest.generation_id, GenerationState.READY)
    generations.activate(manifest.generation_id, expected_version=0)

    payload = repository.resolve(_locator(source, manifest.generation_id))
    assert payload == {
        "source_id": source.source_id,
        "source_revision": source.revision_id,
        "checksum": source.checksum,
        "content": source.content,
    }
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        with source_pool.transaction() as connection:
            connection.execute("""
                UPDATE retrieval.knowledge_source_revisions
                SET title='mutated' WHERE tenant_id=%s AND source_id=%s
            """, (source.tenant_id, source.source_id))


def test_active_pointer_exposes_complete_old_or_new_manifest(source_pool):
    old = _source("旧版退款政策。", "policy")
    old_manifest, generations, repository = _build(
        source_pool, old, "knowledge-generation-old"
    )
    generations.transition(old_manifest.generation_id, GenerationState.READY)
    generations.activate(old_manifest.generation_id, expected_version=0)
    old_receipt = _receipt(repository, old, old_manifest.generation_id)
    assert repository.validate_active(old_receipt) is RequirementStatus.SATISFIED

    new = _source("新版退款政策。", "policy")
    new_manifest, _same_registry, _same_repository = _build(
        source_pool, new, "knowledge-generation-new"
    )
    new_receipt = _receipt(repository, new, new_manifest.generation_id)
    assert repository.validate_active(new_receipt) is RequirementStatus.STALE
    assert repository.validate_active(old_receipt) is RequirementStatus.SATISFIED

    generations.transition(new_manifest.generation_id, GenerationState.READY)
    generations.activate(new_manifest.generation_id, expected_version=1)
    assert repository.validate_active(old_receipt) is RequirementStatus.STALE
    assert repository.validate_active(new_receipt) is RequirementStatus.SATISFIED


def test_t04_knowledge_gate_accepts_only_active_resolvable_revision(source_pool):
    source = _source()
    manifest, generations, repository = _build(
        source_pool, source, "knowledge-generation-coverage"
    )
    generations.transition(manifest.generation_id, GenerationState.READY)
    generations.activate(manifest.generation_id, expected_version=0)
    receipt = _receipt(repository, source, manifest.generation_id)
    policies = AuthorityPolicyRegistry.v1()

    report = RequirementCoverageGate(policies).evaluate(
        (policies.get("knowledge.active_source"),), (receipt,),
        resolvers={"knowledge_search": repository},
        knowledge_revision_validator=repository.validate_active,
        now=NOW,
    )
    assert report.complete is True


def test_legacy_identity_and_non_source_chunk_projection_fail_closed(source_pool):
    with pytest.raises(KnowledgeSourceContractError, match="legacy"):
        SourceRevision.create(
            tenant_id="tenant-knowledge", source_id="legacy-policy",
            title="bad", source_type="text", content="bad", effective_from=NOW,
        )
    source = _source()
    manifest = _manifest(source, "knowledge-generation-invalid")
    generations = PostgresRetrievalGenerationRegistry(source_pool)
    generations.register(_generation(manifest))
    generations.transition(manifest.generation_id, GenerationState.BUILDING)
    invalid_chunk = KnowledgeChunkProjection(
        **{
            **_chunk(source, manifest.generation_id).__dict__,
            "lexical_document": "invented projection",
        }
    )
    with pytest.raises(KnowledgeSourceContractError, match="not a source revision"):
        PostgresKnowledgeSourceRepository(source_pool).write_generation(
            manifest, (source,), (invalid_chunk,),
        )
