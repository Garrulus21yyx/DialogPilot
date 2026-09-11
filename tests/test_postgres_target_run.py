import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from application.admission_contract import ClaimStart
from application.chat_contracts import Failed
from application.inbound_admission import NewInvocationInbound
from application.target_run import TargetRunClaimLost, TargetRunTerminal
from core.identity import IdentityFactory
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
from infrastructure.postgres_conversation import PostgresInvocationRepository
from infrastructure.postgres_target_run import (
    PostgresTargetRunBinder,
    PostgresTargetRunStore,
)


@pytest.fixture()
def target_run_components(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.compatibility_execution_outbox,
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


def _admit_and_bind(pool, suffix="one", *, identity=None, message="查一下订单状态"):
    identity = identity or IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-target-run",
        user_id="user-target-run",
        conversation_id=f"conversation-{suffix}",
        request_id=f"request-{suffix}",
    )
    now = datetime.now(timezone.utc)
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity,
        message,
        {
            "authorization_fingerprint": "auth-v1",
            "approval_decision": "none",
            "interaction_values": "[]",
        },
        now.isoformat(),
        runtime_kind="target",
    ))
    dispatcher = StartOutboxDispatcher(
        PostgresStartOutbox(pool),
        PostgresInvocationRepository(pool),
        PostgresTargetRunBinder(pool),
    )
    result = dispatcher.dispatch_once(ClaimStart(
        "starter",
        now.isoformat(),
        (now + timedelta(seconds=30)).isoformat(),
        1,
        "target",
    ))
    assert result[0].status == "bound"
    return identity


def test_target_run_claim_is_exclusive_and_stale_attempt_is_fenced(
    target_run_components,
):
    pool = target_run_components
    identity = _admit_and_bind(pool)
    store = PostgresTargetRunStore(pool)
    stale = store.claim(worker_id="worker-a", lease_seconds=60)[0]
    assert store.claim(worker_id="worker-b", lease_seconds=60) == ()

    with pool.transaction() as connection:
        connection.execute("""
            UPDATE dialogpilot_app.compatibility_execution_outbox
            SET lease_until=transaction_timestamp() - interval '1 second'
            WHERE workflow_run_id=%s
        """, (str(identity.workflow_run_id),))
    current = store.claim(worker_id="worker-b", lease_seconds=60)[0]
    assert current.attempt == stale.attempt + 1

    terminal = TargetRunTerminal(
        "COMPLETED",
        {"response_id": "response-1", "response": {"response": "完成"}},
        "response-1",
    )
    with pytest.raises(TargetRunClaimLost):
        store.complete(stale, worker_id="worker-a", terminal=terminal)
    store.complete(current, worker_id="worker-b", terminal=terminal)
    assert store.terminal(identity.invocation_key) == terminal
    from application.chat_contracts import Accepted, Completed
    from application.target_run import TargetRunCoordinator
    reader = TargetRunCoordinator(
        None, dispatcher=None, store=PostgresTargetRunStore(pool),
    )
    observed = asyncio.run(reader.await_outcome(Accepted(
        str(identity.workflow_run_id), {"invocation_key": str(identity.invocation_key)},
    )))
    assert observed == Completed("response-1", {"response": "完成"})


def test_released_target_run_is_reclaimed_without_changing_identity(
    target_run_components,
):
    pool = target_run_components
    identity = _admit_and_bind(pool, "retry")
    store = PostgresTargetRunStore(pool)
    first = store.claim(worker_id="worker-a", lease_seconds=60)[0]
    store.release(
        first,
        worker_id="worker-a",
        failure=Failed("process_crash", True, "trace"),
        retry_after_seconds=0,
    )
    second = store.claim(worker_id="worker-b", lease_seconds=60)[0]
    assert second.run_id == identity.workflow_run_id
    assert second.invocation_key == identity.invocation_key
    assert second.attempt == first.attempt + 1
    store.complete(second, worker_id="worker-b", terminal=TargetRunTerminal(
        "COMPLETED", {"response_id": "reply", "response": {"response": "done"}}, "reply"))
    record = store.runtime({"execution_run_id": str(identity.workflow_run_id)})
    assert record["execution_status"] == "COMPLETED"
    assert record["attempt_failures"][str(first.attempt)]["code"] == "process_crash"
    assert record["attempt_failures"][str(first.attempt)]["retryable"] is True


def test_selected_failure_is_immutable_and_reclaimed_for_delivery(target_run_components):
    from application.chat_contracts import StageObservation, StageStatus
    pool = target_run_components
    identity = _admit_and_bind(pool, "selected-failure")
    store = PostgresTargetRunStore(pool)
    first = store.claim(worker_id="a", lease_seconds=60)[0]
    failure = Failed("invalid_decision", False, "trace", stages=(
        StageObservation("recovery", StageStatus.FAILED, {
            "code": "INVALID", "exception_chain": [{"message": "internal-only"}]}),))
    store.select_failure(first, worker_id="a", failure=failure)
    store.select_failure(first, worker_id="a", failure=failure)
    with pytest.raises(TargetRunClaimLost):
        store.select_failure(first, worker_id="a", failure=Failed("other", False, "trace"))
    with pool.transaction() as connection:
        connection.execute("""UPDATE dialogpilot_app.compatibility_execution_outbox
            SET lease_until=transaction_timestamp() - interval '1 second'
            WHERE workflow_run_id=%s""", (str(identity.workflow_run_id),))
    second = store.claim(worker_id="b", lease_seconds=60)[0]
    assert second.selected_failure == failure
    with pytest.raises(TargetRunClaimLost):
        store.select_failure(first, worker_id="a", failure=failure)
    assert "internal-only" not in str(store.runtime({"execution_run_id": str(identity.workflow_run_id)}))


def test_failure_notice_and_run_terminal_replay_agree(target_run_components):
    from application.target_run import TargetRunWorker, outcome_from_terminal
    from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
    from infrastructure.target_chat_adapters import PostgresTargetPublication
    from tests.test_target_chat_cutover import _application
    pool = target_run_components
    identity = _admit_and_bind(pool, "failure-publication")
    store = PostgresTargetRunStore(pool)
    application, _ = _application()
    application._publication = PostgresTargetPublication(PostgresResponseDeliveryService(
        pool, resume_binding_secret="test-resume-secret"))
    async def execute(item, guard):
        return Failed("invalid_decision", False, str(identity.invocation_key))
    async def publish(item, failure):
        return application.completed(identity) or application.publish_failure(identity, failure)
    outcome, = asyncio.run(TargetRunWorker(store, execute, finalize_failure=publish).run_once(worker_id="worker"))
    assert isinstance(outcome, Failed) and outcome.response_id
    assert application.completed(identity) == outcome
    assert outcome_from_terminal(store.terminal(identity.invocation_key)) == outcome


def test_repeated_binding_and_concurrent_claims_have_one_run_and_one_owner(target_run_components):
    pool = target_run_components
    identity = _admit_and_bind(pool, "parallel")

    def bind(_index):
        return PostgresTargetRunBinder(pool).get_or_create(
            invocation_key=identity.invocation_key,
            workflow_run_id=identity.workflow_run_id, pinned_versions={},
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        pointers = list(executor.map(bind, range(8)))
    assert all(pointer == pointers[0] for pointer in pointers)
    assert pointers[0].runtime_kind == "target"

    def claim(index):
        return PostgresTargetRunStore(pool).claim(
            worker_id=f"worker-{index}", lease_seconds=30,
            invocation_key=identity.invocation_key,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = [item for items in executor.map(claim, range(8)) for item in items]
    assert len(claimed) == 1
    assert claimed[0].attempt == 1
    assert claimed[0].run_id == identity.workflow_run_id
    with pool.transaction() as connection:
        count = connection.execute(
            "SELECT count(*) FROM dialogpilot_app.compatibility_execution_outbox"
        ).fetchone()[0]
    assert count == 1


@pytest.mark.parametrize("action", ["renew", "assert_owned", "complete", "release"])
def test_same_worker_cannot_use_a_previous_claim_epoch(target_run_components, action):
    pool = target_run_components
    _admit_and_bind(pool, "epoch")
    store = PostgresTargetRunStore(pool)
    old = store.claim(worker_id="worker", lease_seconds=30)[0]
    store.release(old, worker_id="worker", failure=Failed("retry", True, "trace"), retry_after_seconds=0)
    current = store.claim(worker_id="worker", lease_seconds=30)[0]
    assert current.attempt == old.attempt + 1
    parameters = {"worker_id": "worker"}
    if action == "renew":
        parameters["lease_seconds"] = 30
    elif action == "complete":
        parameters["terminal"] = TargetRunTerminal("COMPLETED", {}, "old")
    elif action == "release":
        parameters["failure"] = Failed("old", True, "trace")
    with pytest.raises(TargetRunClaimLost):
        getattr(store, action)(old, **parameters)


def test_deleted_conversation_cannot_be_claimed(target_run_components):
    pool = target_run_components
    identity = _admit_and_bind(pool, "deleted")
    with pool.transaction() as connection:
        connection.execute("""
            UPDATE dialogpilot_app.conversations
            SET deletion_epoch=deletion_epoch+1, deleted_at=transaction_timestamp()
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (str(identity.tenant_id), str(identity.user_id), str(identity.conversation_id)))
    assert PostgresTargetRunStore(pool).claim(worker_id="worker", lease_seconds=30) == ()


def test_postgres_worker_commits_then_replays_without_reexecuting(target_run_components):
    from application.chat_contracts import Completed
    from application.target_run import TargetRunWorker, outcome_from_terminal

    pool = target_run_components
    identity = _admit_and_bind(pool, "worker")
    store = PostgresTargetRunStore(pool)
    calls = []

    async def execute(item, guard):
        await guard()
        calls.append(item.attempt)
        return Completed("response-1", {"response": "完成"})

    worker = TargetRunWorker(store, execute, lease_seconds=30, heartbeat_seconds=1)
    first = asyncio.run(worker.run_once(worker_id="worker"))
    assert first == (Completed("response-1", {"response": "完成"}),)
    assert outcome_from_terminal(store.terminal(identity.invocation_key)) == first[0]
    assert asyncio.run(worker.run_once(worker_id="worker")) == ()
    assert calls == [1]
    with pool.transaction() as connection:
        row = connection.execute("""
            SELECT acknowledged_at IS NOT NULL, outcome_type,
                   terminal_ref->>'terminal_ref'
            FROM dialogpilot_app.compatibility_execution_outbox WHERE invocation_key=%s
        """, (str(identity.invocation_key),)).fetchone()
    assert row == (True, "COMPLETED", "response-1")
