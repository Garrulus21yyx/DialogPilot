import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from application.admission_contract import ClaimStart
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


def _admit_and_bind(pool, suffix="one"):
    identity = IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-target-run",
        user_id="user-target-run",
        conversation_id=f"conversation-{suffix}",
        request_id=f"request-{suffix}",
    )
    now = datetime.now(timezone.utc)
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity,
        "查一下订单状态",
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
        error_code="process_crash",
        retry_after_seconds=0,
    )
    second = store.claim(worker_id="worker-b", lease_seconds=60)[0]
    assert second.run_id == identity.workflow_run_id
    assert second.invocation_key == identity.invocation_key
    assert second.attempt == first.attempt + 1
