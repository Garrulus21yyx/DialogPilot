"""M1-T04A registry artifact and PostgreSQL pre-write fence tests."""
import json

import psycopg
import pytest

from application.data_location_registry import (
    DataLocationArtifactInvalid,
    DataLocationRegistry,
    DataLocationWriteDenied,
    DataSubjectRef,
    DataWriteIntent,
    DurableWriteKind,
    UnknownDataLocation,
    default_registry_path,
)
from application.conversation_projection import ConversationSubject
from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.data_location_fence import PostgresDataLocationWriteFence
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_projection import (
    PostgresConversationDeletionRepository,
)


CREATED = "2026-09-02T10:00:00+00:00"


@pytest.fixture()
def location_pool(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=3,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.projection_watermarks,
                dialogpilot_app.conversation_projection_outbox,
                dialogpilot_app.conversation_deletion_events,
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


def _identity(suffix="one"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-location",
        user_id="user-location",
        conversation_id=f"conversation-{suffix}",
        request_id=f"request-{suffix}",
    )


def _subject(identity):
    return DataSubjectRef(
        str(identity.tenant_id), str(identity.user_id),
        str(identity.conversation_id),
    )


def _seed(pool, identity):
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity=identity,
        message="question",
        pinned_versions={"bundle": "v1", "runtime": "compat-v1"},
        created_at=CREATED,
    ))


def _intent(
    identity,
    *,
    location_id="location:pg-response-delivery:v1",
    producer="publication",
    schema_version="20260902_0006",
    retention_class="conversation_operational",
    kind=DurableWriteKind.PRODUCER,
    epoch=0,
):
    return DataWriteIntent(
        location_id=location_id,
        producer=producer,
        kind=kind,
        schema_version=schema_version,
        retention_class=retention_class,
        subject=_subject(identity),
        expected_deletion_epoch=epoch,
    )


def test_registry_is_machine_readable_bounded_and_separates_registration_from_write():
    registry = DataLocationRegistry.load(default_registry_path())
    summary = registry.artifact_summary()
    assert summary["version"] == "v6"
    assert summary["location_count"] == 33
    assert summary["write_approved_count"] == 10
    assert registry.get(
        "location:pg-response-delivery:v1"
    ).readiness.value == "WRITE_APPROVED"
    assert registry.get(
        "location:agent-checkpoint:v1"
    ).readiness.value == "REGISTERED"
    assert registry.get(
        "location:knowledge-index:v1"
    ).readiness.value == "WRITE_APPROVED"
    assert registry.get(
        "location:knowledge-source:v1"
    ).owner == "Knowledge"
    with pytest.raises(UnknownDataLocation):
        registry.get("location:unknown:v1")


def test_write_approved_registration_requires_adapter_proof_and_restore_fence(
    tmp_path,
):
    raw = json.loads(
        (default_registry_path().parent / "v1.json").read_text("utf-8")
    )
    raw["locations"][0]["proof_contract_id"] = None
    path = tmp_path / "invalid-registry.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(DataLocationArtifactInvalid, match="lacks deletion/proof"):
        DataLocationRegistry.load(path)


def test_overlay_fingerprint_binds_the_effective_base_artifact(tmp_path):
    source_dir = default_registry_path().parent
    base_path = tmp_path / "v1.json"
    overlay_path = tmp_path / "v2.json"
    base_raw = json.loads((source_dir / "v1.json").read_text("utf-8"))
    overlay_raw = json.loads((source_dir / "v2.json").read_text("utf-8"))
    base_path.write_text(json.dumps(base_raw), encoding="utf-8")
    overlay_path.write_text(json.dumps(overlay_raw), encoding="utf-8")
    original = DataLocationRegistry.load(overlay_path).fingerprint
    base_raw["locations"][0]["allowed_producers"].append("unexpected-writer")
    base_path.write_text(json.dumps(base_raw), encoding="utf-8")
    assert DataLocationRegistry.load(overlay_path).fingerprint != original


def test_unknown_planned_wrong_producer_schema_and_retention_fail_closed():
    registry = DataLocationRegistry.load(default_registry_path())
    identity = _identity()
    with pytest.raises(UnknownDataLocation):
        registry.authorize_contract(_intent(
            identity, location_id="location:not-registered:v1",
        ))
    with pytest.raises(DataLocationWriteDenied, match="not write-approved"):
        registry.authorize_contract(_intent(
            identity,
            location_id="location:agent-checkpoint:v1",
            producer="langgraph-checkpointer",
            schema_version="agent-checkpoint-v1",
        ))
    with pytest.raises(DataLocationWriteDenied, match="producer"):
        registry.authorize_contract(_intent(identity, producer="unknown-writer"))
    with pytest.raises(DataLocationWriteDenied, match="schema"):
        registry.authorize_contract(_intent(identity, schema_version="wrong"))
    with pytest.raises(DataLocationWriteDenied, match="retention"):
        registry.authorize_contract(_intent(
            identity, retention_class="security_audit",
        ))


def test_installed_registry_and_subject_epoch_authorize_existing_location(
    location_pool,
):
    identity = _identity("existing")
    _seed(location_pool, identity)
    authorization = PostgresDataLocationWriteFence(location_pool).authorize(
        _intent(identity),
    )
    assert authorization.registry_version == "v6"
    assert authorization.registry_fingerprint == (
        "cbf99367f299650413996974cf981a58c8485d984d8df9cea7d0d0790babedf3"
    )
    assert authorization.subject_exists is True
    assert authorization.deletion_epoch == 0


def test_only_registered_subject_owner_can_authorize_first_write(location_pool):
    identity = _identity("new-subject")
    fence = PostgresDataLocationWriteFence(location_pool)
    creation = fence.authorize(_intent(
        identity,
        location_id="location:pg-conversation-facts:v1",
        producer="admission",
    ))
    assert creation.subject_exists is False
    with pytest.raises(DataLocationWriteDenied, match="cannot create"):
        fence.authorize(_intent(identity))


def test_tombstone_and_epoch_change_block_late_producer_backfill_and_restore(
    location_pool,
):
    identity = _identity("deleted")
    _seed(location_pool, identity)
    PostgresConversationDeletionRepository(location_pool).delete(
        subject=ConversationSubject(
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id),
        ),
        reason_code="user_erasure", actor="privacy-worker", created_at=CREATED,
    )
    fence = PostgresDataLocationWriteFence(location_pool)
    for kind in (
        DurableWriteKind.PRODUCER,
        DurableWriteKind.BACKFILL,
        DurableWriteKind.DARK_SHADOW,
        DurableWriteKind.RESTORE,
    ):
        with pytest.raises(DataLocationWriteDenied, match="deletion-fenced"):
            fence.authorize(_intent(
                identity, producer="delivery-backfill", kind=kind, epoch=0,
            ))


def test_database_registry_binding_is_immutable_and_migration_runner_verifies_it(
    location_pool, postgres_database_url,
):
    assert PostgresMigrationRunner(postgres_database_url).verify()["head"] == (
            "20260902_0022"
    )
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="immutable"):
        with location_pool.transaction() as connection:
            connection.execute("""
                UPDATE dialogpilot_platform.data_location_registry_revisions
                SET artifact_fingerprint=%s WHERE registry_version='v1'
            """, ("0" * 64,))
