"""Canonical PostgreSQL ResponseDelivery lifecycle tests."""
import pytest

from application.inbound_admission import NewInvocationInbound
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_response_delivery import (
    PostgresResponseDeliveryService,
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
    service = PostgresResponseDeliveryService(
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


def test_published_partial_response_keeps_reconciliation_on_database_replay(compat_components):
    from application.chat_contracts import Reconciling
    from application.target_run import terminal_from_outcome, outcome_from_terminal
    _, identity, service = compat_components
    metadata = {**_metadata(identity), "public_response": {
        "outcome": "reconciling", "execution": "RECONCILING",
        "workflow_run_id": str(identity.workflow_run_id), "next_poll_after": 1.0,
        "task_completed": False,
    }}
    selected = service.select_response(user_id=str(identity.user_id),
        conv_id=str(identity.conversation_id), request_id=str(identity.request_id),
        response_text="The order was found; the separate action outcome is being checked.",
        identity_metadata=metadata)
    recovered = service.completed_for_invocation(identity.invocation_key, user_id=str(identity.user_id))
    assert isinstance(recovered, Reconciling)
    assert recovered.public_status["response_id"] == selected.response_id
    assert not recovered.public_status["task_completed"]
    assert "order was found" in recovered.public_status["response"]
    terminal = terminal_from_outcome(recovered)
    assert terminal.status == "RECONCILING"
    assert outcome_from_terminal(terminal) == recovered


def test_select_retry_ack_read_and_replay_use_postgres_authority(
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


def test_canonical_publication_recovers_complete_public_response(compat_components):
    _, identity, service = compat_components
    metadata = {
        **_metadata(identity),
        "public_response": {
            "request_id": str(identity.request_id),
            "conv_id": str(identity.conversation_id),
            "response": "answer",
            "intent": "order_query",
            "agent_type": "general",
            "escalated": False,
            "latency_ms": 4.0,
            "verification_status": "pass",
            "verified": True,
            "grounded": True,
        },
        "execution_stages": [{
            "stage": "verification", "status": "ok", "detail": {"passed": True},
        }],
    }
    selected = service.select_response(
        user_id=str(identity.user_id), conv_id=str(identity.conversation_id),
        request_id=str(identity.request_id), response_text="answer",
        identity_metadata=metadata,
    )
    recovered = service.completed_for_invocation(
        identity.invocation_key, user_id=str(identity.user_id),
    )
    assert recovered.response_id == selected.response_id
    assert recovered.response["response_id"] == selected.response_id
    assert recovered.response["response_seq"] == selected.seq
    assert recovered.response["delivery_status"] is DeliveryStatus.SELECTED
    assert recovered.response["intent"] == "order_query"
    assert recovered.stages[0].stage == "verification"


def test_target_publication_preserves_failure_diagnostics_on_replay(compat_components):
    from application.response_assembly import ResponseAssembler
    from infrastructure.target_chat_adapters import PostgresTargetPublication
    _, identity, service = compat_components
    adapter = PostgresTargetPublication(service)
    failed = ResponseAssembler()._failed(ValueError('structured_output_incomplete'), 'Safe answer', 'composition_model')
    published = adapter.publish(identity, response_text=failed.text,
        public_response={'verification_reason_code': failed.verification_reason, 'verified': False},
        bundle_version='v1', evidence_sha256='a' * 64, verifier_status='unknown',
        execution_stages=failed.diagnostics)
    replay = adapter.completed(identity)
    assert replay.response_id == published.response_id
    assert replay.stages == failed.diagnostics


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
