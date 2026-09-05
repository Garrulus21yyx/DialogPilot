"""M1-T02C durable whole-invocation compatibility execution proofs."""
from concurrent.futures import ThreadPoolExecutor
import asyncio

import pytest

from application.admission_contract import ClaimStart
from application.compatibility_execution import (
    CompatibilityExecutionClaimLost,
    CompatibilityExecutionTerminal,
    CompatibilityExecutionWorker,
    outcome_from_terminal,
)
from application.chat_contracts import ChatCommand, Completed, Conflict, Failed
from application.compatibility_chat import CompatibilityChatCoordinator
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import (
    PostgresAdmissionUnitOfWork,
    PostgresStartOutbox,
    StartOutboxDispatcher,
)
from infrastructure.postgres_compatibility_execution import (
    PostgresCompatibilityExecutionOutbox,
    PostgresCompatibilityRunBinder,
)
from infrastructure.postgres_conversation import PostgresInvocationRepository
from tests.test_postgres_admission import _identity, _new
from services.evolution import (
    ActiveBundleAssignment,
    PinnedExecutionRefs,
    build_default_bundle,
)


@pytest.fixture()
def compat_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=8,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.compatibility_execution_outbox,
                dialogpilot_app.resume_requested_outbox,
                dialogpilot_app.workflow_start_outbox,
                dialogpilot_app.response_deliveries,
                dialogpilot_app.workflow_invocations,
                dialogpilot_app.conversation_events,
                dialogpilot_app.conversation_turns,
                dialogpilot_app.conversations
            CASCADE
        """)
    try:
        yield pool
    finally:
        pool.close()


def _admit_and_bind(pool, suffix="one"):
    identity = _identity(suffix, conversation=f"compat-{suffix}")
    admission = PostgresAdmissionUnitOfWork(pool)
    admission.admit_new(_new(identity, message=f"message-{suffix}"))
    dispatcher = StartOutboxDispatcher(
        PostgresStartOutbox(pool), PostgresInvocationRepository(pool),
        PostgresCompatibilityRunBinder(pool),
    )
    result = dispatcher.dispatch_once(ClaimStart(
        "start-worker", "2026-09-02T04:00:00+02:00",
        "2026-09-02T04:01:00+02:00", 1,
    ))
    assert result[0].status == "bound"
    return identity


def test_binding_creates_one_durable_work_item_and_reuses_opaque_run(compat_pool):
    identity = _admit_and_bind(compat_pool)
    binder = PostgresCompatibilityRunBinder(compat_pool)
    first = binder.get_or_create(
        invocation_key=identity.invocation_key,
        workflow_run_id=identity.workflow_run_id,
        pinned_versions={"bundle": "bundle-v1"},
    )
    second = binder.get_or_create(
        invocation_key=identity.invocation_key,
        workflow_run_id=identity.workflow_run_id,
        pinned_versions={"bundle": "bundle-v1"},
    )
    assert first == second
    assert (first.runtime_kind, first.runtime_version, first.run_id) == (
        "compat", "chat-application-compat-v1", str(identity.workflow_run_id),
    )
    with compat_pool.transaction() as connection:
        assert connection.execute(
            "SELECT count(*) FROM dialogpilot_app.compatibility_execution_outbox"
        ).fetchone()[0] == 1


def test_online_coordinator_admits_binds_executes_and_replays_one_terminal(compat_pool):
    bundle = build_default_bundle({"worker": {"model": "test"}})
    refs = PinnedExecutionRefs(
        bundle_version=bundle.version,
        bundle_hash=bundle.content_hash,
        route_policy_ref="route-v1",
        knowledge_backend_ref="knowledge-v1",
        knowledge_generation_ref="generation-v1",
        corpus_manifest_ref="corpus-v1",
        retrieval_policy_ref="retrieval-v1",
    )
    assignment = ActiveBundleAssignment(
        primary=bundle, pinned_refs=refs,
    )

    class BundleResolver:
        def resolve(self, _subject):
            return assignment

    class Registry:
        def get(self, version):
            assert version == bundle.version
            return bundle

    class CompletedReader:
        completed = None

        def completed_for_invocation(self, _key, *, user_id):
            assert user_id == "compat-online-user"
            return self.completed

    class Application:
        calls = 0

        async def execute_pinned(
            self, command, identity, *, assignment, publication_guard,
        ):
            self.calls += 1
            await publication_guard()
            assert command.message == "durable message"
            assert command.asset_ids == ("asset-one", "asset-two")
            assert assignment.primary.version == bundle.version
            reader.completed = Completed("publication-online", {
                "request_id": str(identity.request_id),
                "response": "durable answer",
            })
            return Failed(
                "post_publication_projection_failed", False, "trace-online",
            )

    application = Application()
    reader = CompletedReader()
    outbox = PostgresCompatibilityExecutionOutbox(compat_pool)
    coordinator = CompatibilityChatCoordinator(
        application,
        admission=PostgresAdmissionUnitOfWork(compat_pool),
        dispatcher=StartOutboxDispatcher(
            PostgresStartOutbox(compat_pool),
            PostgresInvocationRepository(compat_pool),
            PostgresCompatibilityRunBinder(compat_pool),
        ),
        execution_outbox=outbox,
        bundle_resolver=BundleResolver(),
        bundle_registry=Registry(),
        completed_reader=reader,
        worker_id="online-test",
        execution_lease_seconds=30,
        heartbeat_seconds=5,
    )
    command = ChatCommand(
        message="durable message",
        tenant_id="tenant-online",
        user_id="compat-online-user",
        conv_id="conversation-online",
        request_id="request-online",
        authorization_fingerprint="f" * 64,
        asset_ids=("asset-one", "asset-two"),
    )
    first = asyncio.run(coordinator.handle(command))
    replay = asyncio.run(coordinator.handle(command))
    changed_authority = asyncio.run(coordinator.handle(ChatCommand(
        **{**command.__dict__, "authorization_fingerprint": "e" * 64},
    )))
    assert first == replay
    assert isinstance(first, Completed)
    assert isinstance(changed_authority, Conflict)
    assert changed_authority.code == "IDEMPOTENCY_CONFLICT"
    assert first.response_id == "publication-online"
    assert application.calls == 1
    with compat_pool.transaction() as connection:
        row = connection.execute("""
            SELECT acknowledged_at IS NOT NULL, pinned_versions
            FROM dialogpilot_app.compatibility_execution_outbox job
            JOIN dialogpilot_app.workflow_invocations invocation
              USING (invocation_key)
        """).fetchone()
    assert row[0] is True
    assert row[1]["primary_bundle_version"] == bundle.version


def test_concurrent_workers_claim_exactly_one_epoch(compat_pool):
    identity = _admit_and_bind(compat_pool, "concurrent")
    outbox = PostgresCompatibilityExecutionOutbox(compat_pool)

    def claim(worker):
        return outbox.claim(
            worker_id=worker, lease_seconds=30, invocation_key=identity.invocation_key,
        )

    with ThreadPoolExecutor(max_workers=4) as workers:
        claims = list(workers.map(claim, [f"worker-{index}" for index in range(4)]))
    claimed = [items[0] for items in claims if items]
    assert len(claimed) == 1
    assert claimed[0].message == "message-concurrent"
    assert claimed[0].attempt == 1
    assert claimed[0].pinned_versions["bundle"] == "bundle-v1"


def test_old_claim_epoch_cannot_ack_or_renew_after_release_and_reclaim(compat_pool):
    identity = _admit_and_bind(compat_pool, "fence")
    outbox = PostgresCompatibilityExecutionOutbox(compat_pool)
    old = outbox.claim(
        worker_id="same-worker", lease_seconds=30,
        invocation_key=identity.invocation_key,
    )[0]
    outbox.release(old, worker_id="same-worker", error_code="crash")
    current = outbox.claim(
        worker_id="same-worker", lease_seconds=30,
        invocation_key=identity.invocation_key,
    )[0]
    assert current.attempt == old.attempt + 1
    with pytest.raises(CompatibilityExecutionClaimLost):
        outbox.renew(old, worker_id="same-worker", lease_seconds=30)
    with pytest.raises(CompatibilityExecutionClaimLost):
        outbox.acknowledge(
            old, worker_id="same-worker",
            terminal=CompatibilityExecutionTerminal(
                "COMPLETED", {"response_id": "stale"}, "stale",
            ),
        )


def test_terminal_ack_is_atomic_with_invocation_and_replayable(compat_pool):
    identity = _admit_and_bind(compat_pool, "terminal")
    outbox = PostgresCompatibilityExecutionOutbox(compat_pool)
    item = outbox.claim(
        worker_id="worker", lease_seconds=30,
        invocation_key=identity.invocation_key,
    )[0]
    terminal = CompatibilityExecutionTerminal(
        "COMPLETED", {"response_id": "response-1", "response": {"ok": True}},
        "response-1",
    )
    outbox.acknowledge(item, worker_id="worker", terminal=terminal)
    assert outbox.terminal(identity.invocation_key) == terminal
    assert outbox.claim(
        worker_id="worker-2", lease_seconds=30,
        invocation_key=identity.invocation_key,
    ) == ()
    with compat_pool.transaction() as connection:
        row = connection.execute("""
            SELECT acknowledged_at IS NOT NULL, outcome_type,
                   terminal_ref->>'terminal_ref'
            FROM dialogpilot_app.compatibility_execution_outbox
            WHERE invocation_key=%s
        """, (str(identity.invocation_key),)).fetchone()
    assert row == (True, "COMPLETED", "response-1")


def test_deleted_conversation_fences_pending_execution(compat_pool):
    identity = _admit_and_bind(compat_pool, "deleted")
    with compat_pool.transaction() as connection:
        connection.execute("""
            UPDATE dialogpilot_app.conversations
            SET deletion_epoch=deletion_epoch+1,
                deleted_at=transaction_timestamp()
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ))
    assert PostgresCompatibilityExecutionOutbox(compat_pool).claim(
        worker_id="worker", lease_seconds=30,
        invocation_key=identity.invocation_key,
    ) == ()


def test_worker_guards_publication_and_replays_durable_terminal(compat_pool):
    identity = _admit_and_bind(compat_pool, "worker")
    outbox = PostgresCompatibilityExecutionOutbox(compat_pool)
    calls = []

    async def execute(item, guard):
        calls.append(item.attempt)
        await guard()
        return Completed(
            "response-worker", {"request_id": item.request_id, "response": "ok"},
        )

    worker = CompatibilityExecutionWorker(
        outbox, execute, lease_seconds=30, heartbeat_seconds=1,
    )
    outcomes = asyncio.run(worker.run_once(
        worker_id="runtime-worker", invocation_key=identity.invocation_key,
    ))
    assert outcomes[0].response_id == "response-worker"
    terminal = outbox.terminal(identity.invocation_key)
    assert terminal is not None
    assert outcome_from_terminal(terminal) == outcomes[0]
    assert asyncio.run(worker.run_once(
        worker_id="runtime-worker", invocation_key=identity.invocation_key,
    )) == ()
    assert calls == [1]


def test_retryable_failure_releases_same_job_for_next_attempt(compat_pool):
    identity = _admit_and_bind(compat_pool, "retryable")
    outbox = PostgresCompatibilityExecutionOutbox(compat_pool)

    async def execute(item, _guard):
        return Failed(
            "provider_unavailable", True, f"attempt-{item.attempt}",
        )

    worker = CompatibilityExecutionWorker(
        outbox, execute, lease_seconds=30, heartbeat_seconds=1,
        retry_after_seconds=0,
    )
    first = asyncio.run(worker.run_once(
        worker_id="worker", invocation_key=identity.invocation_key,
    ))
    second = asyncio.run(worker.run_once(
        worker_id="worker", invocation_key=identity.invocation_key,
    ))
    assert [item.correlation_id for item in (*first, *second)] == [
        "attempt-1", "attempt-2",
    ]
    assert outbox.terminal(identity.invocation_key) is None
