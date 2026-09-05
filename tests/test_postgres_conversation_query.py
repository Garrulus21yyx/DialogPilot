"""M1-T05 scoped transcript, status, watermark and close-command tests."""
import psycopg
import pytest

from application.admission_contract import AdmissionStatus, ExecutionStatus
from application.delivery_contract import ConnectorCapability, DeliveryStatusV1
from application.inbound_admission import NewInvocationInbound
from application.publication import (
    FinalResponseCommand,
    InteractionRequestCommand,
    PublicationPolicy,
)
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_conversation_query import (
    ConversationQueryAccessDenied,
    PostgresConversationQueryService,
)
from infrastructure.postgres_publication import PostgresPublicationService


CREATED = "2026-09-02T11:00:00+00:00"


@pytest.fixture()
def query_components(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=5,
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
    identity = _identity("one")
    _seed(pool, identity)
    try:
        yield (
            pool, identity, PostgresConversationQueryService(pool),
            PostgresPublicationService(pool, resume_binding_secret="query-secret"),
        )
    finally:
        pool.close()


def _identity(suffix, conversation="query-conversation"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-query",
        user_id="user-query",
        conversation_id=conversation,
        request_id=f"request-{suffix}",
    )


def _seed(pool, identity, message="contact me at user@example.com token=abcd1234"):
    return PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity=identity,
        message=message,
        pinned_versions={"bundle": "v1", "runtime": "compat-v1"},
        created_at=CREATED,
    ))


def _policy():
    return PublicationPolicy(
        connector_capability=ConnectorCapability.NONE,
        max_attempts=1,
        retry_policy_version="query-no-retry-v1",
        reconcile_deadline="2026-09-03T11:00:00+00:00",
    )


def _final(identity):
    return FinalResponseCommand(
        invocation_key=identity.invocation_key,
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        response_text="card 4111 1111 1111 1111 answer",
        candidate_id="candidate-query",
        producer="compat-runtime",
        verifier_status="verified",
        verification={"passed": True},
        evidence_sha256="a" * 64,
        bundle_version="v1",
        index_manifest_sha256="b" * 64,
        created_at="2026-09-02T11:00:01+00:00",
        policy=_policy(),
    )


def _interaction(identity):
    return InteractionRequestCommand(
        invocation_key=identity.invocation_key,
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        signal_id="signal-query", signal_version=1,
        challenge="approve?", resume_schema={"type": "boolean"},
        created_at="2026-09-02T11:00:01+00:00", policy=_policy(),
    )


def test_transcript_is_scoped_paginated_and_only_exposes_redacted_public_fields(
    query_components,
):
    _, identity, query, publication = query_components
    publication.select_final_response(_final(identity))
    first = query.list_turns(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), limit=1,
    )
    second = query.list_turns(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), after_seq=first[-1].seq,
    )
    assert len(first) == len(second) == 1
    assert first[0].role == "inbound"
    assert first[0].content == (
        "contact me at [REDACTED_EMAIL] token=[REDACTED]"
    )
    assert second[0].role == "assistant"
    assert second[0].content == "card [REDACTED_CARD]answer"
    assert set(first[0].__dict__) == {
        "seq", "role", "content", "created_at", "request_id",
    }
    with pytest.raises(ConversationQueryAccessDenied):
        query.list_turns(
            tenant_id=str(identity.tenant_id), user_id="other-user",
            conversation_id=str(identity.conversation_id),
        )


def test_public_event_cursor_replays_only_committed_client_events(
    query_components,
):
    _, identity, query, publication = query_components
    accepted = query.list_public_events(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
    )
    assert [event.event_type for event in accepted.events] == ["run.accepted"]
    assert "user@example.com" not in accepted.events[0].to_sse()

    final = publication.select_final_response(_final(identity))
    resumed = query.list_public_events(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        after_event_id=accepted.last_event_id,
    )
    assert [event.event_type for event in resumed.events] == [
        "response.committed",
    ]
    assert resumed.events[0].payload["response_id"] == final.record.publication_id
    assert query.list_public_events(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        after_event_id=accepted.last_event_id,
    ) == resumed


def test_public_event_cursor_pagination_matches_continuous_read_without_gaps(
    query_components,
):
    _, identity, query, publication = query_components
    interaction = publication.publish_interaction_request(_interaction(identity))
    final = publication.select_final_response(_final(identity))

    continuous = query.list_public_events(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
    )
    cursor = None
    replayed = []
    while True:
        page = query.list_public_events(
            tenant_id=str(identity.tenant_id),
            user_id=str(identity.user_id),
            conversation_id=str(identity.conversation_id),
            after_event_id=cursor,
            limit=1,
        )
        if not page.events:
            break
        replayed.extend(page.events)
        cursor = page.last_event_id

    expected_types = [
        "run.accepted", "interaction.requested", "response.committed",
    ]
    assert [event.event_type for event in continuous.events] == expected_types
    assert replayed == list(continuous.events)
    assert len({event.event_id for event in replayed}) == len(replayed)
    assert replayed[1].payload["publication_id"] == (
        interaction.record.publication_id
    )
    assert replayed[2].payload["response_id"] == final.record.publication_id

    # Rejoining from the last committed cursor is a read-only empty replay.
    assert query.list_public_events(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        after_event_id=cursor,
        limit=1,
    ).events == ()


def test_unknown_or_cross_scope_public_cursor_requires_state_reload(
    query_components,
):
    _, identity, query, _ = query_components
    page = query.list_public_events(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        after_event_id="event-from-another-scope",
    )
    assert page.events == ()
    assert page.last_event_id is None
    assert page.reset_required is True


def test_invocation_projection_keeps_admission_waiting_completion_and_delivery_separate(
    query_components,
):
    _, identity, query, publication = query_components
    admitted = query.invocation_status(
        str(identity.invocation_key),
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
    )
    assert admitted.admission_status is AdmissionStatus.START_QUEUED
    assert admitted.execution_status is None
    assert admitted.delivery_status is None

    interaction = publication.publish_interaction_request(_interaction(identity))
    waiting = query.invocation_status(
        str(identity.invocation_key),
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
    )
    assert waiting.execution_status is ExecutionStatus.WAITING
    assert waiting.pending_signal["publication_id"] == (
        interaction.record.publication_id
    )
    assert waiting.delivery_status is DeliveryStatusV1.SELECTED

    final = publication.select_final_response(_final(identity))
    completed = query.invocation_status(
        str(identity.invocation_key),
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
    )
    assert completed.execution_status is ExecutionStatus.COMPLETED
    assert completed.pending_signal is None
    assert completed.final_response["response_id"] == final.record.publication_id
    assert completed.delivery_status is DeliveryStatusV1.SELECTED


def test_compat_runtime_is_read_only_and_terminal_publication_has_priority(
    query_components,
):
    pool, identity, _, publication = query_components
    observed = []

    def runtime_reader(invocation):
        observed.append(invocation["workflow_run_id"])
        return {"execution_status": "RUNNING", "runtime_kind": "compat"}

    query = PostgresConversationQueryService(pool, runtime_reader=runtime_reader)
    running = query.invocation_status(
        str(identity.invocation_key),
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
    )
    assert running.execution_status is ExecutionStatus.RUNNING
    publication.select_final_response(_final(identity))
    completed = query.invocation_status(
        str(identity.invocation_key),
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
    )
    assert completed.execution_status is ExecutionStatus.COMPLETED
    assert len(observed) == 2


def test_finalize_progress_only_observes_watermarks_and_does_not_close(
    query_components,
):
    pool, identity, query, _ = query_components
    pending = query.projection_progress(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
    )
    assert pending.target_event_seq == 1
    assert pending.caught_up is False
    with pool.transaction() as connection:
        connection.execute("""
            INSERT INTO dialogpilot_app.projection_watermarks (
                projection_name, generation, tenant_id, user_id, conversation_id,
                last_event_seq, last_event_id, source_deletion_epoch, updated_at
            )
            SELECT projection_name, generation, %s, %s, %s, 1, 'event', 0, %s
            FROM dialogpilot_app.projection_registry
        """, (
            str(identity.tenant_id), str(identity.user_id),
            str(identity.conversation_id), CREATED,
        ))
    caught_up = query.projection_progress(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
    )
    assert caught_up.caught_up is True
    second = _identity("after-finalize")
    assert _seed(pool, second, "later message") is not None


def test_close_is_audited_idempotent_and_fences_subsequent_writes(
    query_components,
):
    pool, identity, query, _ = query_components
    first = query.close_conversation(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), reason_code="user_closed",
        actor=str(identity.user_id), created_at="2026-09-02T11:05:00+00:00",
    )
    replay = query.close_conversation(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), reason_code="changed",
        actor=str(identity.user_id), created_at="2026-09-02T11:06:00+00:00",
    )
    assert first.already_closed is False
    assert replay == type(replay)(first.close_id, True, first.closed_at)
    with pool.transaction() as connection:
        facts = connection.execute("""
            SELECT
              (SELECT count(*) FROM dialogpilot_app.conversation_close_events),
              (SELECT count(*) FROM dialogpilot_app.conversation_events
               WHERE event_type='CONVERSATION_CLOSED'),
              (SELECT array_agg(projection_name ORDER BY projection_name)
               FROM dialogpilot_app.conversation_projection_outbox
               WHERE event_id IN (
                 SELECT event_id FROM dialogpilot_app.conversation_events
                 WHERE event_type='CONVERSATION_CLOSED'
               ))
        """).fetchone()
    assert facts == (
        1, 1, ["fact_extraction", "thread_summary", "working_window"],
    )
    with pytest.raises(
        psycopg.errors.ObjectNotInPrerequisiteState, match="conversation is closed",
    ):
        _seed(pool, _identity("late-close"))
