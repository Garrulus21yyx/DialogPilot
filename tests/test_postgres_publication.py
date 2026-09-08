"""M1-T03 atomic publication and canonical delivery integration properties."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from application.delivery_contract import (
    ConnectorCapability,
    DeliveryEvent,
    DeliveryStatusV1,
    InvalidDeliveryTransition,
)
from application.inbound_admission import NewInvocationInbound
from application.conversation_state import ConversationState
from application.publication import (
    FinalResponseCommand,
    HumanReplyCommand,
    InteractionRequestCommand,
    PublicationApplyStatus,
    PublicationKind,
    PublicationPolicy,
)
from core.identity import IdentityFactory
from infrastructure.postgres import (
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_admission import PostgresAdmissionUnitOfWork
from infrastructure.postgres_publication import (
    PostgresDeliveryRepository,
    PostgresPublicationService,
    PublicationConflictError,
)


CREATED = "2026-09-02T05:00:00+02:00"
DEADLINE = "2026-09-03T05:00:00+02:00"


@pytest.fixture()
def publication_components(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=6,
    ))
    pool.open()
    _reset(pool)
    try:
        identity = _seed(pool)
        yield (
            pool,
            identity,
            PostgresPublicationService(pool, resume_binding_secret="test-secret"),
            PostgresDeliveryRepository(pool),
        )
    finally:
        pool.close()


def _identity(suffix="one"):
    return IdentityFactory(lambda: "unused").create_invocation(
        tenant_id="tenant-publication",
        user_id="user-publication",
        conversation_id=f"conversation-{suffix}",
        request_id=f"request-{suffix}",
    )


def _seed(pool, suffix="one"):
    identity = _identity(suffix)
    PostgresAdmissionUnitOfWork(pool).admit_new(NewInvocationInbound(
        identity=identity,
        message="question",
        pinned_versions={"bundle": "bundle-v1", "runtime": "compat-v1"},
        created_at=CREATED,
    ))
    return identity


def _policy(capability=ConnectorCapability.IDEMPOTENT_SEND):
    return PublicationPolicy(
        connector_capability=capability,
        max_attempts=3,
        retry_policy_version="retry-v1",
        reconcile_deadline=DEADLINE,
    )


def _final(identity, text="answer"):
    return FinalResponseCommand(
        invocation_key=identity.invocation_key,
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        response_text=text,
        candidate_id="candidate-1",
        producer="graph-finalizer-v1",
        verifier_status="verified",
        verification={"policy": "grounded-v1", "passed": True},
        evidence_sha256="a" * 64,
        bundle_version="bundle-v1",
        index_manifest_sha256="b" * 64,
        created_at=CREATED,
        policy=_policy(),
        expected_state_fingerprint=ConversationState.empty(tenant_id=str(identity.tenant_id),
            user_id=str(identity.user_id), conversation_id=str(identity.conversation_id)).fingerprint,
    )


def _seed_waits(pool, identity, approval_id="approval-signal"):
    from application.agent_result import RequestedField
    from application.conversation_state import PendingApprovalState, PendingInteractionState, WorkstreamState, WorkstreamStatus
    from infrastructure.postgres_target_runtime import PostgresConversationStateStore
    from tests.test_work_control import _item
    store = PostgresConversationStateStore(pool)
    state = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    if state.pending_approval is not None:
        return state
    approval_work = _item("approval-control", 1, work_item_id="write")
    input_work = _item("input-control", 1, work_item_id="read")
    active = state.accept_work_items((approval_work, input_work), invocation_key=str(identity.invocation_key))
    waiting = replace(active,
        workstreams=(WorkstreamState("approval-work", "order_logistics", "action:v1", "APPROVE",
                                    WorkstreamStatus.WAITING_APPROVAL, 1),),
        pending_approval=PendingApprovalState(approval_id, 2, "approval-work", "write",
            "action:v1", "operation", "order", "1", DEADLINE,
            suspended_work_items=(approval_work,), origin_work_item_id="write", control=approval_work.control),
        pending_interaction=PendingInteractionState("fields-signal", 3,
            (RequestedField("color", "read", "string"),), (), suspended_work_items=(input_work,),
            checkpoint_thread_id="input-checkpoint"))
    assert store.compare_and_set(state, waiting)
    return waiting


def _interaction(identity, pool):
    state = _seed_waits(pool, identity)
    return InteractionRequestCommand(
        invocation_key=identity.invocation_key,
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        signal_id="approval-signal",
        signal_version=2,
        challenge="Please confirm",
        resume_schema={"type": "boolean", "interaction_kind": "APPROVAL"},
        created_at=CREATED,
        policy=_policy(ConnectorCapability.QUERY_RECEIPT),
        expected_state_fingerprint=state.fingerprint,
    )


def test_interaction_uses_the_same_transactional_control_check(publication_components):
    pool, identity, service, _ = publication_components
    command = replace(_interaction(identity, pool), expected_state_fingerprint="stale")
    with pytest.raises(PublicationConflictError, match="conversation state"):
        service.publish_interaction_request(command)
    with pool.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM dialogpilot_app.response_deliveries").fetchone()[0] == 0


@pytest.mark.parametrize("kind", ["FIELDS", "APPROVAL", "COMPOUND"])
def test_interaction_replay_retains_diagnostics_without_exposing_them_in_challenge(publication_components, kind):
    from types import SimpleNamespace
    from application.chat_contracts import StageObservation, StageStatus
    from infrastructure.target_chat_adapters import PostgresTargetPublication
    pool, identity, service, _ = publication_components
    observation = StageObservation("conversation_recovery", StageStatus.FAILED,
                                   {"code": "RECOVERY_PROVIDER_FAILURE"})
    command = replace(_interaction(identity, pool), resume_schema={"interaction_kind": kind},
                      execution_stages=(observation.to_dict(),))
    if kind == "FIELDS":
        command = replace(command, signal_id="fields-signal", signal_version=3)
    elif kind == "COMPOUND":
        command = replace(command, related_signals=(("fields-signal", 3),))
    first = service.publish_interaction_request(command)
    adapter = PostgresTargetPublication(SimpleNamespace(pool=pool,
        completed_for_invocation=lambda *args, **kwargs: None))
    replay = adapter.completed(identity)
    assert replay.stages == (observation,)
    assert replay.kind == kind
    assert replay.interaction_publication_id == first.record.publication_id
    assert service.publish_interaction_request(command).record.publication_id == first.record.publication_id
    with pool.transaction() as connection:
        payload = connection.execute("SELECT payload FROM dialogpilot_app.response_deliveries "
            "WHERE publication_id=%s", (first.record.publication_id,)).fetchone()[0]
    assert payload["challenge"] == command.challenge
    assert payload["execution_stages"] == [observation.to_dict()]


def test_compound_interaction_records_both_signals_in_one_publication(publication_components):
    from types import SimpleNamespace
    from infrastructure.target_chat_adapters import PostgresTargetPublication
    pool, identity, service, _ = publication_components
    command = replace(_interaction(identity, pool), related_signals=(("fields-signal", 3),),
                      resume_schema={"interaction_kind": "COMPOUND"})
    first = service.publish_interaction_request(command)
    replay = service.publish_interaction_request(command)
    assert replay.record.publication_id == first.record.publication_id
    adapter = PostgresTargetPublication(SimpleNamespace(pool=pool))
    assert adapter.has_interaction(identity, signal_id="approval-signal", signal_version=2)
    assert adapter.has_interaction(identity, signal_id="fields-signal", signal_version=3)
    assert not adapter.has_interaction(identity, signal_id="fields-signal", signal_version=2)
    assert not adapter.has_interaction(replace(identity, user_id="other"), signal_id="fields-signal", signal_version=3)
    with pool.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM dialogpilot_app.response_deliveries").fetchone()[0] == 1


def _human(identity):
    return HumanReplyCommand(
        tenant_id=str(identity.tenant_id),
        user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id),
        ticket_id="ticket-1",
        handoff_id="handoff-1",
        human_message_id="message-1",
        text="Human answer",
        created_at=CREATED,
        policy=_policy(ConnectorCapability.NONE),
    )


def _reset(pool):
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


def _publication_counts(pool):
    with pool.transaction() as connection:
        return tuple(connection.execute(
            f"SELECT count(*) FROM {table}",
        ).fetchone()[0] for table in (
            "dialogpilot_app.response_deliveries",
            "dialogpilot_app.delivery_outbox",
            "dialogpilot_app.delivery_receipts",
        ))


def test_final_selection_is_one_atomic_terminal_fact_and_conflicts_on_change(
    publication_components,
):
    pool, identity, service, _ = publication_components
    first = service.select_final_response(_final(identity))
    replay = service.select_final_response(_final(identity))
    conflict = service.select_final_response(_final(identity, "changed"))

    assert first.status is PublicationApplyStatus.APPLIED
    assert replay.status is PublicationApplyStatus.ALREADY_APPLIED
    assert conflict.status is PublicationApplyStatus.IDEMPOTENCY_CONFLICT
    assert first.record.kind is PublicationKind.FINAL_RESPONSE
    assert _publication_counts(pool) == (1, 1, 0)
    terminal = service.final_commit(first)
    assert terminal.response_id == first.record.publication_id
    assert terminal.response["response"] == "answer"
    with pool.transaction() as connection:
        roles = connection.execute(
            "SELECT role FROM dialogpilot_app.conversation_turns ORDER BY seq"
        ).fetchall()
        events = connection.execute(
            "SELECT event_type FROM dialogpilot_app.conversation_events ORDER BY seq"
        ).fetchall()
    assert roles == [("inbound",), ("assistant",)]
    assert events == [("REQUEST_ACCEPTED",), ("FINAL_RESPONSE_SELECTED",)]


def test_concurrent_same_publication_commits_one_fact_without_sequence_holes(
    publication_components,
):
    pool, identity, service, _ = publication_components
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(
            lambda _: service.select_final_response(_final(identity)), range(12),
        ))
    assert sum(
        item.status is PublicationApplyStatus.APPLIED for item in results
    ) == 1
    assert sum(
        item.status is PublicationApplyStatus.ALREADY_APPLIED for item in results
    ) == 11
    assert _publication_counts(pool) == (1, 1, 0)
    with pool.transaction() as connection:
        sequences = connection.execute(
            "SELECT next_turn_seq, next_event_seq, next_publication_seq FROM "
            "dialogpilot_app.conversations WHERE conversation_id=%s",
            (str(identity.conversation_id),),
        ).fetchone()
    assert sequences == (3, 3, 2)


def test_retry_timestamp_does_not_change_publication_idempotency(
    publication_components,
):
    _, identity, service, _ = publication_components
    command = _final(identity)
    assert service.select_final_response(
        command,
    ).status is PublicationApplyStatus.APPLIED
    retry = replace(command, created_at="2026-09-02T05:30:00+02:00")
    assert service.select_final_response(
        retry,
    ).status is PublicationApplyStatus.ALREADY_APPLIED


@pytest.mark.parametrize("stage", [
    "after_outbound_turn", "after_outbound_event",
    "after_delivery_fact", "after_delivery_outbox",
])
def test_crash_at_any_publication_stage_rolls_back_the_whole_selection(
    postgres_database_url, stage,
):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    pool.open()
    _reset(pool)
    identity = _seed(pool, f"crash-{stage}")

    def fail(current):
        if current == stage:
            raise RuntimeError("injected publication crash")

    try:
        service = PostgresPublicationService(
            pool, resume_binding_secret="test-secret", fault_hook=fail,
        )
        with pytest.raises(RuntimeError, match="injected publication crash"):
            service.select_final_response(_final(identity))
        assert _publication_counts(pool) == (0, 0, 0)
        assert PostgresPublicationService(
            pool, resume_binding_secret="test-secret",
        ).select_final_response(_final(identity)).status is PublicationApplyStatus.APPLIED
    finally:
        pool.close()


def test_interaction_and_human_publications_preserve_nonterminal_meaning(
    publication_components,
):
    pool, identity, service, _ = publication_components
    interaction = service.publish_interaction_request(_interaction(identity, pool))
    human = service.publish_human_reply(_human(identity))
    assert interaction.record.kind is PublicationKind.INTERACTION_REQUEST
    assert human.record.kind is PublicationKind.HUMAN_REPLY
    assert service.publish_interaction_request(
        _interaction(identity, pool),
    ).status is PublicationApplyStatus.ALREADY_APPLIED
    assert service.publish_human_reply(
        _human(identity),
    ).status is PublicationApplyStatus.ALREADY_APPLIED

    with pytest.raises(ValueError, match="only final response"):
        service.final_commit(interaction)
    with pool.transaction() as connection:
        invocation = connection.execute(
            "SELECT admission_status, terminal_ref FROM "
            "dialogpilot_app.workflow_invocations WHERE invocation_key=%s",
            (str(identity.invocation_key),),
        ).fetchone()
        signature = connection.execute(
            "SELECT resume_binding_signature FROM "
            "dialogpilot_app.response_deliveries WHERE publication_id=%s",
            (interaction.record.publication_id,),
        ).fetchone()[0]
    assert invocation == ("START_QUEUED", None)
    assert len(signature) == 64
    assert _publication_counts(pool) == (2, 2, 0)


def test_delivery_receipts_are_idempotent_and_ack_read_are_monotonic(
    publication_components,
):
    pool, identity, service, deliveries = publication_components
    publication = service.select_final_response(_final(identity)).record
    started = deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="receipt-start",
        event=DeliveryEvent.SEND_STARTED,
        payload={"attempt": 1},
        created_at="2026-09-02T05:01:00+02:00",
    )
    assert started.status is DeliveryStatusV1.DELIVERING
    delivered = deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="receipt-delivered",
        event=DeliveryEvent.SEND_CONFIRMED,
        payload={"provider_id": "provider-1"},
        created_at="2026-09-02T05:02:00+02:00",
    )
    assert delivered.status is DeliveryStatusV1.DELIVERED
    read = deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="receipt-read",
        event=DeliveryEvent.READ_ACK,
        payload={"provider_id": "provider-1"},
        created_at="2026-09-02T05:03:00+02:00",
    )
    assert read.status is DeliveryStatusV1.READ
    late = deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="receipt-late-delivered",
        event=DeliveryEvent.RECEIPT_DELIVERED,
        payload={"provider_id": "provider-1"},
        created_at="2026-09-02T05:04:00+02:00",
    )
    assert late.status is DeliveryStatusV1.READ
    replay = deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="receipt-read",
        event=DeliveryEvent.READ_ACK,
        payload={"provider_id": "provider-1"},
        created_at="2026-09-02T06:00:00+02:00",
    )
    assert replay.status is DeliveryStatusV1.READ
    assert _publication_counts(pool) == (1, 1, 4)
    with pytest.raises(PublicationConflictError, match="content changed"):
        deliveries.apply_event(
            publication_id=publication.publication_id,
            receipt_id="receipt-read",
            event=DeliveryEvent.READ_ACK,
            payload={"provider_id": "changed"},
            created_at="2026-09-02T06:00:00+02:00",
        )


def test_unknown_send_effect_never_resends_without_connector_guarantee(
    publication_components,
):
    _, identity, service, deliveries = publication_components
    command = replace(
        _final(identity), policy=_policy(ConnectorCapability.NONE),
    )
    publication = service.select_final_response(command).record
    deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="none-start",
        event=DeliveryEvent.SEND_STARTED,
        payload={}, created_at="2026-09-02T05:01:00+02:00",
    )
    uncertain = deliveries.apply_event(
        publication_id=publication.publication_id,
        receipt_id="none-disconnect",
        event=DeliveryEvent.SEND_DISCONNECTED,
        payload={}, created_at="2026-09-02T05:02:00+02:00",
    )
    assert uncertain.status is DeliveryStatusV1.DELIVERY_UNCERTAIN
    with pytest.raises(InvalidDeliveryTransition, match="RETRY_DUE"):
        deliveries.apply_event(
            publication_id=publication.publication_id,
            receipt_id="none-retry",
            event=DeliveryEvent.RETRY_DUE,
            payload={}, created_at="2026-09-02T05:03:00+02:00",
        )
