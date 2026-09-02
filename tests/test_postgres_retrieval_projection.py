"""M2-PF01 canonical outbox, replay, shadow and deletion-fence proofs."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import psycopg
import pytest

from application.conversation_projection import ConversationSubject
from application.hybrid_retrieval import (
    GenerationConflict,
    GenerationState,
    RetrievalCorpus,
)
from application.knowledge_source import KnowledgeSourceManifest, SourceRevision
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_source import PostgresKnowledgeSourceRepository
from infrastructure.postgres_projection import PostgresConversationDeletionRepository
from infrastructure.postgres_retrieval_projection import PostgresCanonicalRetrievalProjector
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry
from tests.test_knowledge_source_postgres import _chunk, _generation, _manifest


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def projection_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url, min_size=1, max_size=3))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                retrieval.canonical_projection_receipts,
                retrieval.canonical_projection_outbox,
                retrieval.knowledge_source_chunk_specs,
                retrieval.knowledge_source_manifest_entries,
                retrieval.knowledge_source_manifests,
                retrieval.knowledge_source_revisions,
                retrieval.knowledge_chunk_search,
                retrieval.service_episode_search,
                retrieval.retrieval_generation_pointers,
                retrieval.retrieval_generation_registry,
                dialogpilot_app.conversations CASCADE
        """)
    try:
        yield pool
    finally:
        pool.close()


def _source(suffix="one"):
    return SourceRevision.create(
        tenant_id="tenant-projection", source_id=f"source-{suffix}",
        title="policy", source_type="text", content=f"退款政策 {suffix}",
        effective_from=NOW,
    )


def _enqueue(pool, generation_id, suffix="one"):
    source = _source(suffix)
    manifest = KnowledgeSourceManifest.build(
        tenant_id=source.tenant_id, backend_id="pg-knowledge-v1",
        generation_id=generation_id, scope="public", locale="zh-CN",
        product="", sources=(source,), reviewer_manifest_ref="review/ref",
    )
    registry = PostgresRetrievalGenerationRegistry(pool)
    registry.register(_generation(manifest))
    registry.transition(generation_id, GenerationState.BUILDING)
    PostgresKnowledgeSourceRepository(pool).write_generation(
        manifest, (source,), (_chunk(source, generation_id),),
    )
    with pool.transaction() as connection:
        event_id = connection.execute("""
            SELECT event_id FROM retrieval.canonical_projection_outbox
            WHERE generation_id=%s
        """, (generation_id,)).fetchone()[0]
        assert connection.execute("""
            SELECT count(*) FROM retrieval.knowledge_chunk_search
            WHERE generation_id=%s
        """, (generation_id,)).fetchone()[0] == 0
    return registry, manifest, source, str(event_id)


def test_backfill_only_enqueues_and_projection_is_exactly_replayable(projection_pool):
    _, manifest, _, event_id = _enqueue(projection_pool, "projection-replay")
    projector = PostgresCanonicalRetrievalProjector(projection_pool)
    first = projector.project(event_id)
    second = projector.project(event_id)
    assert (first.code.value, first.candidate_count) == ("APPLIED", 1)
    assert (second.code.value, second.candidate_count) == ("ALREADY_APPLIED", 1)
    with projection_pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(*) FROM retrieval.knowledge_chunk_search
            WHERE generation_id=%s AND tenant_id=%s AND scope='public'
        """, (manifest.generation_id, manifest.tenant_id)).fetchone()[0] == 1
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState, match="identity is immutable",
    ):
        with projection_pool.transaction() as connection:
            connection.execute("""
                UPDATE retrieval.canonical_projection_outbox
                SET source_ref=%s WHERE event_id=%s
            """, ("a" * 64, event_id))


def test_closed_generation_and_canonical_drift_fail_closed(projection_pool):
    registry, manifest, _, event_id = _enqueue(projection_pool, "projection-closed")
    registry.transition(manifest.generation_id, GenerationState.READY)
    result = PostgresCanonicalRetrievalProjector(projection_pool).project(event_id)
    assert result.code.value == "GENERATION_NOT_BUILDING"
    with projection_pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(*) FROM retrieval.knowledge_chunk_search
            WHERE generation_id=%s
        """, (manifest.generation_id,)).fetchone()[0] == 0

    _, manifest2, _, event2 = _enqueue(projection_pool, "projection-drift", "two")
    with projection_pool.transaction() as connection:
        original = connection.execute("""
            SELECT corpus,tenant_id,backend_id,generation_id,source_ref,
                   source_revision FROM retrieval.canonical_projection_outbox
            WHERE event_id=%s
        """, (event2,)).fetchone()
        connection.execute(
            "DELETE FROM retrieval.canonical_projection_outbox WHERE event_id=%s",
            (event2,),
        )
        connection.execute("""
            INSERT INTO retrieval.canonical_projection_outbox (
                event_id,corpus,tenant_id,backend_id,generation_id,source_ref,
                source_revision,source_fingerprint,deletion_epoch
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0)
        """, (event2, *original, "f" * 64))
    drift = PostgresCanonicalRetrievalProjector(projection_pool).project(event2)
    assert drift.code.value == "CANONICAL_SOURCE_DRIFT"
    assert manifest2.generation_id == "projection-drift"


def test_shadow_generation_never_moves_active_pointer(projection_pool):
    registry, first, _, first_event = _enqueue(projection_pool, "active-one")
    PostgresCanonicalRetrievalProjector(projection_pool).project(first_event)
    registry.transition(first.generation_id, GenerationState.READY)
    registry.activate(first.generation_id, expected_version=0)
    _, shadow, _, shadow_event = _enqueue(projection_pool, "dark-shadow", "shadow")
    PostgresCanonicalRetrievalProjector(projection_pool).project(shadow_event)
    with projection_pool.transaction() as connection:
        pointer = connection.execute("""
            SELECT active_generation_id FROM retrieval.retrieval_generation_pointers
            WHERE corpus='KNOWLEDGE' AND backend_id=%s
        """, (first.backend_id,)).fetchone()[0]
    assert pointer == first.generation_id
    assert shadow.generation_id != pointer


def test_concurrent_generation_activation_cas_allows_only_one_active_winner(
    projection_pool,
):
    registry, first, _, first_event = _enqueue(projection_pool, "active-race-one")
    PostgresCanonicalRetrievalProjector(projection_pool).project(first_event)
    registry.transition(first.generation_id, GenerationState.READY)
    _, second, _, second_event = _enqueue(
        projection_pool, "active-race-two", "race-two",
    )
    PostgresCanonicalRetrievalProjector(projection_pool).project(second_event)
    registry.transition(second.generation_id, GenerationState.READY)

    def activate(generation_id):
        try:
            return registry.activate(generation_id, expected_version=0).active_generation_id
        except GenerationConflict:
            return "VERSION_CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(
            activate, (first.generation_id, second.generation_id),
        ))

    assert outcomes.count("VERSION_CONFLICT") == 1
    winner = next(item for item in outcomes if item != "VERSION_CONFLICT")
    with projection_pool.transaction() as connection:
        pointer = connection.execute("""
            SELECT active_generation_id, version
            FROM retrieval.retrieval_generation_pointers
            WHERE corpus='KNOWLEDGE' AND backend_id=%s
        """, (first.backend_id,)).fetchone()
        active_count = connection.execute("""
            SELECT count(*) FROM retrieval.retrieval_generation_registry
            WHERE corpus='KNOWLEDGE' AND backend_id=%s AND state='ACTIVE'
        """, (first.backend_id,)).fetchone()[0]
    assert pointer == (winner, 1)
    assert active_count == 1


def test_episode_enqueue_and_late_projection_are_deletion_fenced(projection_pool):
    registry = PostgresRetrievalGenerationRegistry(projection_pool)
    source = _source("episode-generation")
    manifest = _manifest(source, "episode-generation")
    generation = _generation(manifest)
    episode_generation = type(generation)(**{
        **generation.__dict__, "corpus": RetrievalCorpus.SERVICE_EPISODE,
        "generation_id": "episode-generation",
    })
    registry.register(episode_generation)
    registry.transition(episode_generation.generation_id, GenerationState.BUILDING)
    with projection_pool.transaction() as connection:
        connection.execute("""
            INSERT INTO dialogpilot_app.conversations
                (tenant_id,user_id,conversation_id)
            VALUES ('tenant-projection','user-one','conversation-one')
        """)
        connection.execute("""
            INSERT INTO retrieval.canonical_projection_outbox (
                event_id, corpus, tenant_id, backend_id, generation_id,
                source_ref, source_revision, source_fingerprint,
                subject_user_id, subject_conversation_id, deletion_epoch
            ) VALUES (
                'episode-event','SERVICE_EPISODE','tenant-projection',%s,%s,
                'episode-one','revision-one',%s,'user-one','conversation-one',0
            )
        """, (episode_generation.backend_id, episode_generation.generation_id, "a" * 64))
        connection.execute("""
            INSERT INTO retrieval.service_episode_search (
                candidate_id,tenant_id,user_id,source_conversation_id,
                backend_id,generation_id,episode_id,episode_revision,
                outcome_receipt_ref,provenance_sha256,deletion_epoch,
                verified_at,lexical_document,projected_at
            ) VALUES ('existing-episode','tenant-projection','user-one',
                'conversation-one',%s,%s,'episode-one','revision-one',
                'receipt-one',%s,0,now(),'退款完成',now())
        """, (
            episode_generation.backend_id, episode_generation.generation_id,
            "c" * 64,
        ))
    PostgresConversationDeletionRepository(projection_pool).delete(
        subject=ConversationSubject(
            "tenant-projection", "user-one", "conversation-one",
        ), reason_code="user_erasure", actor="privacy-worker",
        created_at=NOW.isoformat(),
    )
    result = PostgresCanonicalRetrievalProjector(projection_pool).project("episode-event")
    assert result.code.value == "SUBJECT_DELETION_FENCED"
    with projection_pool.transaction() as connection:
        assert connection.execute("""
            SELECT count(*) FROM retrieval.service_episode_search
            WHERE source_conversation_id='conversation-one'
        """).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            connection.execute("""
                INSERT INTO retrieval.canonical_projection_outbox (
                    event_id, corpus, tenant_id, backend_id, generation_id,
                    source_ref, source_revision, source_fingerprint,
                    subject_user_id, subject_conversation_id, deletion_epoch
                ) VALUES ('late','SERVICE_EPISODE','tenant-projection',%s,%s,
                    'episode-two','revision-one',%s,
                    'user-one','conversation-one',0)
            """, (episode_generation.backend_id, episode_generation.generation_id, "b" * 64))
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState, match="deletion-fenced",
    ):
        with projection_pool.transaction() as connection:
            connection.execute("""
                INSERT INTO retrieval.service_episode_search (
                    candidate_id,tenant_id,user_id,source_conversation_id,
                    backend_id,generation_id,episode_id,episode_revision,
                    outcome_receipt_ref,provenance_sha256,deletion_epoch,
                    verified_at,lexical_document,projected_at
                ) VALUES ('stale-restore','tenant-projection','user-one',
                    'conversation-one',%s,%s,'episode-restored','revision-one',
                    'receipt-old',%s,0,now(),'stale backup',now())
            """, (
                episode_generation.backend_id, episode_generation.generation_id,
                "e" * 64,
            ))


def test_live_episode_without_owner_resolver_is_typed_and_writes_nothing(
    projection_pool,
):
    source = _source("missing-resolver")
    manifest = _manifest(source, "episode-no-resolver")
    generation = _generation(manifest)
    episode = type(generation)(**{
        **generation.__dict__, "corpus": RetrievalCorpus.SERVICE_EPISODE,
    })
    registry = PostgresRetrievalGenerationRegistry(projection_pool)
    registry.register(episode)
    registry.transition(episode.generation_id, GenerationState.BUILDING)
    with projection_pool.transaction() as connection:
        connection.execute("""
            INSERT INTO dialogpilot_app.conversations
                (tenant_id,user_id,conversation_id)
            VALUES ('tenant-projection','user-live','conversation-live')
        """)
        connection.execute("""
            INSERT INTO retrieval.canonical_projection_outbox (
                event_id,corpus,tenant_id,backend_id,generation_id,source_ref,
                source_revision,source_fingerprint,subject_user_id,
                subject_conversation_id,deletion_epoch
            ) VALUES ('no-resolver','SERVICE_EPISODE','tenant-projection',%s,%s,
                'episode-live','revision-one',%s,'user-live','conversation-live',0)
        """, (episode.backend_id, episode.generation_id, "d" * 64))
    result = PostgresCanonicalRetrievalProjector(projection_pool).project("no-resolver")
    assert result.code.value == "RESOLVER_UNAVAILABLE"
    with projection_pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM retrieval.service_episode_search"
        ).fetchone()[0] == 0
