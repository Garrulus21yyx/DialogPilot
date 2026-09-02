"""M1-T03A deterministic SQLite export/backfill/shadow reconciliation tests."""
import sqlite3

import pytest

from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.response_delivery_cutover import (
    LegacyContractError,
    LegacyResponseDeliveryExporter,
    PostgresResponseDeliveryBackfill,
    read_snapshot,
    write_snapshot,
)
from services.response_delivery import DeliveryStatus, ResponseDeliveryService


CREATED = "2026-09-02T06:00:00+00:00"


@pytest.fixture()
def cutover_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.publication_revision_events,
                dialogpilot_app.delivery_receipts,
                dialogpilot_app.delivery_outbox,
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


def _identity(suffix, conversation="legacy-conversation"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-legacy",
        user_id="user-legacy",
        conversation_id=conversation,
        request_id=f"request-{suffix}",
    )


def _seed_target(pool, identity):
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity=identity,
        message=f"question for {identity.request_id}",
        pinned_versions={"bundle": "legacy", "runtime": "compat-v1"},
        created_at=CREATED,
    ))


def _create_legacy(path, identities):
    service = ResponseDeliveryService(str(path))
    deliveries = []
    for identity in identities:
        deliveries.append(service.select_response(
            user_id=str(identity.user_id),
            conv_id=str(identity.conversation_id),
            request_id=str(identity.request_id),
            response_text=f"answer for {identity.request_id}",
            identity_metadata=identity.metadata(),
        ))
    service.acknowledge(
        deliveries[1].response_id,
        user_id=str(identities[1].user_id),
        status=DeliveryStatus.DELIVERED,
    )
    service.acknowledge(
        deliveries[2].response_id,
        user_id=str(identities[2].user_id),
        status=DeliveryStatus.READ,
    )
    return deliveries


def test_export_is_replayable_and_rejects_missing_authoritative_identity(tmp_path):
    path = tmp_path / "legacy.db"
    identity = _identity("missing", conversation="missing-scope")
    service = ResponseDeliveryService(str(path))
    service.select_response(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="answer",
    )
    with pytest.raises(LegacyContractError, match="authoritative identity"):
        LegacyResponseDeliveryExporter().export(str(path), exported_at=CREATED)


def test_snapshot_round_trip_preserves_stable_ids_and_checksums(tmp_path):
    path = tmp_path / "legacy.db"
    identities = [
        _identity("one"), _identity("two"), _identity("three"),
    ]
    deliveries = _create_legacy(path, identities)
    exporter = LegacyResponseDeliveryExporter()
    first = exporter.export(str(path), exported_at=CREATED)
    second = exporter.export(str(path), exported_at=CREATED)
    artifact = tmp_path / "snapshot.json"
    write_snapshot(first, str(artifact))

    assert first == second == read_snapshot(str(artifact))
    assert first.row_count == 3
    assert first.status_counts == {
        "DELIVERED": 1, "DELIVERY_UNCERTAIN": 1, "READ": 1,
    }
    assert [row.response_id for row in first.rows] == [
        delivery.response_id for delivery in deliveries
    ]
    assert all(row.target_outbox_id for row in first.rows)


def test_atomic_backfill_and_shadow_reconcile_every_status_hash_and_id(
    cutover_pool, tmp_path,
):
    identities = [
        _identity("one"), _identity("two"), _identity("three"),
    ]
    for identity in identities:
        _seed_target(cutover_pool, identity)
    path = tmp_path / "legacy.db"
    deliveries = _create_legacy(path, identities)
    snapshot = LegacyResponseDeliveryExporter().export(
        str(path), exported_at=CREATED,
    )
    backfill = PostgresResponseDeliveryBackfill(cutover_pool)
    report = backfill.apply(snapshot)

    assert report.matched is True
    assert report.source_count == report.target_count == 3
    assert report.source_status_counts == report.target_status_counts
    assert report.source_ids_sha256 == report.target_ids_sha256
    assert report.source_content_sha256 == report.target_content_sha256
    assert backfill.apply(snapshot) == report
    with cutover_pool.transaction() as connection:
        rows = connection.execute("""
            SELECT publication_id, status, connector_capability, attempt,
                   max_attempts, acknowledged_at IS NOT NULL
            FROM dialogpilot_app.response_deliveries
            JOIN dialogpilot_app.delivery_outbox USING (publication_id)
            ORDER BY seq
        """).fetchall()
    assert rows == [
        (deliveries[0].response_id, "DELIVERY_UNCERTAIN", "NONE", 0, 1, True),
        (deliveries[1].response_id, "DELIVERED", "NONE", 0, 1, True),
        (deliveries[2].response_id, "READ", "NONE", 0, 1, True),
    ]


def test_missing_target_invocation_rolls_back_all_backfill_facts(
    cutover_pool, tmp_path,
):
    identities = [
        _identity("present", conversation="atomic-conversation"),
        _identity("missing", conversation="atomic-conversation"),
    ]
    _seed_target(cutover_pool, identities[0])
    path = tmp_path / "legacy.db"
    service = ResponseDeliveryService(str(path))
    for identity in identities:
        service.select_response(
            user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
            request_id=str(identity.request_id), response_text="answer",
            identity_metadata=identity.metadata(),
        )
    snapshot = LegacyResponseDeliveryExporter().export(str(path))

    with pytest.raises(LegacyContractError, match="invocation binding"):
        PostgresResponseDeliveryBackfill(cutover_pool).apply(snapshot)
    with cutover_pool.transaction() as connection:
        counts = tuple(connection.execute(
            f"SELECT count(*) FROM dialogpilot_app.{table}",
        ).fetchone()[0] for table in (
            "response_deliveries", "delivery_outbox",
        ))
        legacy_events = connection.execute(
            "SELECT count(*) FROM dialogpilot_app.conversation_events "
            "WHERE event_type='LEGACY_FINAL_RESPONSE_IMPORTED'"
        ).fetchone()[0]
    assert counts == (0, 0)
    assert legacy_events == 0


def test_identity_scope_conflict_is_rejected_before_target_write(tmp_path):
    path = tmp_path / "legacy.db"
    identity = _identity("conflict", conversation="conflict-scope")
    service = ResponseDeliveryService(str(path))
    delivery = service.select_response(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="answer",
        identity_metadata=identity.metadata(),
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE response_deliveries SET user_id='different-user' "
            "WHERE response_id=?", (delivery.response_id,),
        )
    with pytest.raises(LegacyContractError, match="conflicts on user_id"):
        LegacyResponseDeliveryExporter().export(str(path))


def test_multiple_legacy_final_responses_for_one_invocation_fail_closed(tmp_path):
    path = tmp_path / "legacy.db"
    identity = _identity("duplicate", conversation="duplicate-final")
    service = ResponseDeliveryService(str(path))
    for text in ("first", "second"):
        service.select_response(
            user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
            request_id=str(identity.request_id), response_text=text,
            identity_metadata=identity.metadata(),
        )
    with pytest.raises(LegacyContractError, match="duplicate final invocation_key"):
        LegacyResponseDeliveryExporter().export(str(path))
