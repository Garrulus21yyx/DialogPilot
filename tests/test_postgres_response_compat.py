"""M1-T05 assistant-only compatibility API over canonical PostgreSQL delivery."""
import pytest

from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.delivery_binding import DeliveryBindingConflict
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_response_compat import (
    PostgresResponseDeliveryCompatibilityService,
)
from services.response_delivery import (
    DeliveryStatus,
    ResponseNotFoundError,
)


CREATED = "2026-09-02T12:00:00+00:00"


@pytest.fixture()
def compat_components(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=4,
    ))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("""
            TRUNCATE TABLE
                dialogpilot_app.conversation_close_events,
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
        connection.execute("""
            UPDATE dialogpilot_platform.delivery_repository_binding
            SET state='POSTGRES_ACTIVE', generation=generation+1,
                freeze_id='compat-test', snapshot_sha256=%s,
                switched_at=%s, updated_at=%s
            WHERE singleton=TRUE
        """, ("c" * 64, CREATED, CREATED))
    identity = IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-compat", user_id="user-compat",
        conversation_id="conversation-compat", request_id="request-compat",
    )
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity=identity, message="question",
        pinned_versions={"bundle": "v1", "runtime": "compat-v1"},
        created_at=CREATED,
    ))
    timestamps = iter((
        "2026-09-02T12:01:00+00:00",
        "2026-09-02T12:02:00+00:00",
        "2026-09-02T12:03:00+00:00",
        "2026-09-02T12:04:00+00:00",
    ))
    service = PostgresResponseDeliveryCompatibilityService(
        pool, resume_binding_secret="compat-secret", clock=lambda: next(timestamps),
    )
    try:
        yield pool, identity, service
    finally:
        pool.close()


def _metadata(identity):
    return {
        **identity.metadata(),
        "candidate_id": "candidate-compat",
        "producer": "chat-application-compat-v1",
        "verifier_status": "pass",
        "verification": {"status": "pass", "grounded": True},
        "evidence_sha256": "a" * 64,
        "bundle_version": "v1",
        "index_manifest_sha256": "b" * 64,
        "projection_disposition": "normal",
    }


def test_select_retry_ack_read_and_replay_keep_legacy_shape_on_pg_authority(
    compat_components,
):
    _, identity, service = compat_components
    first = service.select_response(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="answer",
        identity_metadata=_metadata(identity),
    )
    replay = service.select_response(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="answer",
        identity_metadata=_metadata(identity),
    )
    assert replay.response_id == first.response_id
    assert replay.request_id == str(identity.request_id)
    assert first.status is DeliveryStatus.SELECTED
    delivered = service.acknowledge(
        first.response_id, user_id=str(identity.user_id),
        status=DeliveryStatus.DELIVERED,
    )
    read = service.acknowledge(
        first.response_id, user_id=str(identity.user_id), status=DeliveryStatus.READ,
    )
    assert delivered.status is DeliveryStatus.DELIVERED
    assert read.status is DeliveryStatus.READ
    assert service.list_after(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
    )[0].response_id == first.response_id
    assert service.stats() == {"selected": 0, "delivered": 0, "read": 1}


def test_cross_user_response_is_not_enumerable(compat_components):
    _, identity, service = compat_components
    response = service.select_response(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="answer",
        identity_metadata=_metadata(identity),
    )
    with pytest.raises(ResponseNotFoundError):
        service.acknowledge(
            response.response_id, user_id="other-user",
            status=DeliveryStatus.READ,
        )


def test_compat_writer_fails_closed_if_binding_is_not_postgres(
    compat_components,
):
    pool, identity, service = compat_components
    with pool.transaction() as connection:
        connection.execute("""
            UPDATE dialogpilot_platform.delivery_repository_binding
            SET state='SQLITE_ACTIVE', generation=generation+1,
                freeze_id=NULL, snapshot_sha256=NULL, switched_at=NULL,
                updated_at=%s WHERE singleton=TRUE
        """, (CREATED,))
    with pytest.raises(DeliveryBindingConflict, match="not active"):
        service.select_response(
            user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
            request_id=str(identity.request_id), response_text="answer",
            identity_metadata=_metadata(identity),
        )
