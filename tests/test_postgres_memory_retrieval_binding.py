"""M4-T04B2 atomic Memory retrieval binding and rollback proofs."""
from dataclasses import replace

import psycopg
import pytest

from application.hybrid_retrieval import GenerationConflict
from application.memory_retrieval_policy import (
    MemoryRetrievalBinding,
    MemoryRetrievalConsumerMode,
    MemoryRetrievalTarget,
)
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_memory_retrieval_binding import (
    PostgresMemoryRetrievalBindingRepository,
)


@pytest.fixture()
def binding_repository(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=3,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("TRUNCATE retrieval.memory_retrieval_bindings")
    try:
        yield pool, PostgresMemoryRetrievalBindingRepository(pool)
    finally:
        pool.close()


def _legacy():
    return MemoryRetrievalTarget(
        "a" * 64, "LEGACY_CHROMA_BM25_V1", "legacy-backend-v1",
        "raw-memory-v4",
    )


def _target():
    return MemoryRetrievalTarget(
        "b" * 64, "POSTGRES_HYBRID_V1", "pg-backend-generation-1",
        "service-episode-generation-1",
    )


def test_initialize_shadow_is_idempotent_but_conflicting_tuple_fails(
    binding_repository,
):
    _pool, repository = binding_repository
    first = repository.initialize_shadow(
        "tenant-1", active=_legacy(), candidate=_target(),
    )
    replay = repository.initialize_shadow(
        "tenant-1", active=_legacy(), candidate=_target(),
    )
    assert first == replay == repository.get("tenant-1")
    with pytest.raises(GenerationConflict, match="differs"):
        repository.initialize_shadow(
            "tenant-1", active=_legacy(),
            candidate=replace(_target(), corpus_generation="different"),
        )


def test_cas_pins_complete_tuple_and_stale_writer_cannot_split_it(
    binding_repository,
):
    _pool, repository = binding_repository
    pinned = repository.initialize_shadow(
        "tenant-1", active=_legacy(), candidate=_target(),
    )
    canary = MemoryRetrievalBinding(
        MemoryRetrievalConsumerMode.PINNED_CANARY,
        active=pinned.active, previous=pinned.previous,
        candidate=pinned.candidate, version=2,
    )
    repository.compare_and_swap("tenant-1", canary, expected_version=1)
    with pytest.raises(GenerationConflict, match="CAS"):
        repository.compare_and_swap(
            "tenant-1", replace(canary, version=2), expected_version=1,
        )
    current = repository.get("tenant-1")
    assert current == canary
    # The already-pinned invocation snapshot remains immutable.
    assert pinned.mode is MemoryRetrievalConsumerMode.SHADOW
    assert pinned.version == 1


def test_rollback_restores_policy_backend_and_corpus_in_one_version(
    binding_repository,
):
    _pool, repository = binding_repository
    repository.initialize_shadow(
        "tenant-1", active=_legacy(), candidate=_target(),
    )
    rolled = repository.rollback("tenant-1", expected_version=1)
    assert rolled.mode is MemoryRetrievalConsumerMode.LEGACY
    assert rolled.active == rolled.previous == _legacy()
    assert rolled.candidate is None
    assert rolled.version == 2
    assert repository.get("tenant-1") == rolled


def test_database_rejects_version_jump_even_outside_repository(binding_repository):
    pool, repository = binding_repository
    repository.initialize_shadow(
        "tenant-1", active=_legacy(), candidate=_target(),
    )
    with pool.transaction() as connection, pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
    ):
        connection.execute("""
            UPDATE retrieval.memory_retrieval_bindings
            SET version=version+2 WHERE tenant_id='tenant-1'
        """)
