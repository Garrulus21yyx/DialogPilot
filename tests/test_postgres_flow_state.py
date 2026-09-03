"""Positive persistence contract for the conversation flow-state owner."""
from concurrent.futures import ThreadPoolExecutor
import uuid

import pytest

from application.flow_state import FlowStateError
from application.turn_state import (
    ActiveFlowRef,
    FlowBinding,
    FlowDefinitionRef,
    PrincipalScope,
)
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_flow_state import PostgresFlowStateStore


@pytest.fixture()
def flow_state_owner(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url,
        min_size=1,
        max_size=4,
    ))
    pool.open()
    suffix = uuid.uuid4().hex
    principal = PrincipalScope(
        "tenant-flow",
        "user-flow",
        f"conversation-{suffix}",
    )
    with pool.transaction() as connection:
        connection.execute("""
            INSERT INTO dialogpilot_app.conversations (
                tenant_id,user_id,conversation_id
            ) VALUES (%s,%s,%s)
        """, (
            principal.tenant_id,
            principal.user_id,
            principal.conversation_id,
        ))
    try:
        yield pool, PostgresFlowStateStore(pool), principal
    finally:
        pool.close()


def _refund_flow(principal: PrincipalScope, order_id: str, *, version: int):
    return ActiveFlowRef(
        definition=FlowDefinitionRef("refund_status", "v1"),
        instance_id="refund-status-1",
        state_version=version,
        principal_fingerprint=principal.fingerprint,
        bindings=(FlowBinding.create("order_id", order_id),),
    )


def test_flow_state_round_trip_and_same_version_has_one_cas_winner(
    flow_state_owner,
):
    _, store, principal = flow_state_owner
    empty = store.load(principal)
    first = empty.next(
        active_flows=(_refund_flow(principal, "DP1234", version=1),),
        pending_slot=None,
    )

    assert store.compare_and_set(empty, first) is True
    current = store.load(principal)
    assert current == first
    assert current.active_flows[0].bindings[0].value == "DP1234"

    candidates = (
        current.next(
            active_flows=(_refund_flow(principal, "DP1234", version=2),),
            pending_slot=None,
        ),
        current.next(
            active_flows=(_refund_flow(principal, "DP5678", version=2),),
            pending_slot=None,
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = tuple(workers.map(
            lambda state: store.compare_and_set(current, state),
            candidates,
        ))

    assert sorted(results) == [False, True]
    assert store.load(principal).aggregate.version == 2


def test_conversation_tombstone_removes_flow_state(flow_state_owner):
    pool, store, principal = flow_state_owner
    empty = store.load(principal)
    state = empty.next(
        active_flows=(_refund_flow(principal, "DP1234", version=1),),
        pending_slot=None,
    )
    assert store.compare_and_set(empty, state) is True

    with pool.transaction() as connection:
        connection.execute("""
            UPDATE dialogpilot_app.conversations
            SET deleted_at=transaction_timestamp(),
                deletion_epoch=deletion_epoch+1
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            principal.tenant_id,
            principal.user_id,
            principal.conversation_id,
        ))
        remaining = connection.execute("""
            SELECT count(*) FROM dialogpilot_app.conversation_flow_state
            WHERE tenant_id=%s AND user_id=%s AND conversation_id=%s
        """, (
            principal.tenant_id,
            principal.user_id,
            principal.conversation_id,
        )).fetchone()[0]

    assert remaining == 0
    with pytest.raises(FlowStateError, match="unavailable"):
        store.load(principal)
