"""The current state owner survives retirement of the pre-Target aggregate."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest

from application.conversation_state import ConversationStateError, WorkstreamState, WorkstreamStatus
from application.data_location_registry import (
    DataLocationRegistry, DataLocationWriteDenied, DataSubjectRef, DataWriteIntent,
    DurableWriteKind, default_registry_path,
)
from core.identity import TenantId, UserId, ConversationId
from infrastructure.postgres import (
    MigrationDriftError, PostgresMigrationRunner, PostgresPool, PostgresPoolConfig,
)
from infrastructure.postgres_target_runtime import PostgresConversationStateStore


def test_retired_location_rejects_producer_and_restore():
    registry = DataLocationRegistry.load(default_registry_path())
    for kind in DurableWriteKind:
        with pytest.raises(DataLocationWriteDenied, match="not write-approved"):
            registry.authorize_contract(DataWriteIntent(
                "location:conversation-flow-state:v1", "flow-state-store", kind,
                "conversation-flow-state-v1", "conversation_operational",
                DataSubjectRef("tenant", "user", "conversation"), 0,
            ))


@pytest.mark.parametrize("write_after_retirement", [False, True])
def test_migration_permissions_follow_the_registry_at_each_revision(
    monkeypatch, write_after_retirement,
):
    def revision(name, writes):
        return SimpleNamespace(revision=name, module=SimpleNamespace(
            subject_linked_write=writes,
            data_location_ids=("location:conversation-flow-state:v1",) if writes else (),
        ))

    revisions = [
        revision("20260905_0035", write_after_retirement),
        revision("20260903_0029", True),
        revision("20260902_0006", False),
    ]
    runner = PostgresMigrationRunner("postgresql://localhost/not-used")
    monkeypatch.setattr(runner, "_script", lambda: SimpleNamespace(
        walk_revisions=lambda **kwargs: iter(revisions),
    ))
    if write_after_retirement:
        with pytest.raises(MigrationDriftError, match="not write-approved"):
            runner._validate_data_location_metadata()
    else:
        runner._validate_data_location_metadata()


def test_upgrade_preserves_target_state_cas_and_deletion_boundary(
    fresh_postgres_database_url,
):
    runner = PostgresMigrationRunner(fresh_postgres_database_url)
    runner.upgrade_to("20260905_0034")
    pool = PostgresPool(PostgresPoolConfig(
        fresh_postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    scope = (TenantId("retirement-tenant"), UserId("user"), ConversationId("conversation"))
    try:
        store = PostgresConversationStateStore(pool)
        initial = store.load(*scope)
        active = initial.start_workstream(WorkstreamState(
            "work", "order_logistics", "order_lookup:v1", "QUERY",
            WorkstreamStatus.ACTIVE, 1,
        ))
        assert store.compare_and_set(initial, active)
        with pool.transaction() as connection:
            connection.execute("""
                INSERT INTO dialogpilot_app.conversation_flow_state
                (tenant_id,user_id,conversation_id,aggregate_id,state,version,deletion_epoch)
                VALUES (%s,%s,%s,'legacy-test',
                    '{"schema_version":"conversation-flow-state-v1"}',1,0)
            """, scope)

        runner.upgrade()
        assert runner.upgrade() == runner.verify()
        assert store.load(*scope) == active
        with pool.transaction() as connection:
            assert connection.execute("""
                SELECT to_regclass('dialogpilot_app.conversation_flow_state'),
                    to_regprocedure('dialogpilot_app.purge_flow_state_on_delete()'),
                    to_regprocedure('dialogpilot_app.guard_conversation_flow_state()')
            """).fetchone() == (None, None, None)

        candidates = (
            active.complete_workstream("work", expected_version=1),
            active.cancel_workstream("work", expected_version=1),
        )
        barrier = Barrier(2)

        def commit(candidate):
            barrier.wait(timeout=10)
            return store.compare_and_set(active, candidate)

        with ThreadPoolExecutor(max_workers=2) as workers:
            accepted = tuple(workers.map(commit, candidates))
        assert sorted(accepted) == [False, True]
        winner = candidates[accepted.index(True)]
        assert store.load(*scope) == winner
        with pool.transaction() as connection:
            connection.execute("""
                UPDATE dialogpilot_app.conversations
                SET deleted_at=transaction_timestamp(), deletion_epoch=deletion_epoch+1
                WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
            """, scope)
        with pytest.raises(ConversationStateError, match="deleted"):
            store.load(*scope)
        with pytest.raises(ConversationStateError, match="deleted"):
            store.compare_and_set(initial, active)
    finally:
        pool.close()
