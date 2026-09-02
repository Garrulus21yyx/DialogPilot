"""M4-T04 direct-cutover binding and one-way activation proofs."""
from dataclasses import replace

import psycopg
import pytest

from application.hybrid_retrieval import GenerationConflict
from application.memory_retrieval_policy import (
    MemoryRetrievalBinding,
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


def _target(suffix="one"):
    return MemoryRetrievalTarget(
        "b" * 64, "POSTGRES_HYBRID_V1", f"pg-backend-{suffix}",
        f"service-episode-{suffix}",
    )


def test_initialize_disabled_target_is_idempotent_without_legacy_tuple(
    binding_repository,
):
    _pool, repository = binding_repository
    first = repository.initialize("tenant-1", target=_target())
    replay = repository.initialize("tenant-1", target=_target())
    assert first == replay == repository.get("tenant-1")
    assert first.enabled is False
    assert not hasattr(first, "previous")
    assert not hasattr(first, "candidate")
    with pytest.raises(GenerationConflict, match="differs"):
        repository.initialize("tenant-1", target=_target("different"))


def test_target_can_change_by_cas_only_before_direct_cutover(binding_repository):
    _pool, repository = binding_repository
    repository.initialize("tenant-1", target=_target())
    updated = repository.replace_before_cutover(
        "tenant-1", _target("accepted"), expected_version=1,
    )
    assert updated.version == 2
    assert updated.target == _target("accepted")
    with pytest.raises(GenerationConflict, match="CAS"):
        repository.replace_before_cutover(
            "tenant-1", _target("stale"), expected_version=1,
        )


def test_activation_is_one_way_and_enabled_target_is_immutable(binding_repository):
    pool, repository = binding_repository
    repository.initialize("tenant-1", target=_target())
    active = repository.activate("tenant-1", expected_version=1)
    assert active.enabled is True
    assert active.version == 2
    assert repository.get("tenant-1") == active
    with pytest.raises(GenerationConflict, match="activation CAS"):
        repository.activate("tenant-1", expected_version=2)
    with pytest.raises(GenerationConflict, match="CAS"):
        repository.replace_before_cutover(
            "tenant-1", _target("other"), expected_version=2,
        )
    with pool.transaction() as connection, pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState,
    ):
        connection.execute("""
            UPDATE retrieval.memory_retrieval_bindings
            SET enabled=FALSE,version=version+1 WHERE tenant_id='tenant-1'
        """)


def test_application_binding_activation_preserves_single_target():
    pending = MemoryRetrievalBinding(_target(), False, 1)
    active = pending.activate()
    assert active == replace(pending, enabled=True, version=2)
    with pytest.raises(ValueError, match="already enabled"):
        active.activate()
