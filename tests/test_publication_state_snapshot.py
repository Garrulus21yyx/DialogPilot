"""Publication freshness is distinct from historical execution permission."""
from dataclasses import replace

import pytest

from application.conversation_state import WorkControlStatus
from application.publication import PublicationApplyStatus
from infrastructure.postgres_publication import PublicationConflictError
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from tests.test_postgres_publication import publication_components, _final
from tests.test_work_control import _item


@pytest.mark.parametrize("transition", ["cancel", "revise", "independent"])
def test_current_snapshot_can_explain_old_results_and_stale_snapshot_cannot_publish(
    publication_components, transition,
):
    pool, identity, publication, _ = publication_components
    store = PostgresConversationStateStore(pool)
    before = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    original = _item("product", 1, work_item_id="product-v1")
    active = before.accept_work_items((original,), invocation_key=str(identity.invocation_key))
    assert store.compare_and_set(before, active)
    if transition == "cancel":
        current = active.close_work_control(original.control, status=WorkControlStatus.CANCELLED)
    else:
        item = (_item("product", 2, work_item_id="product-v2") if transition == "revise"
                else _item("independent", 1, work_item_id="other-v1"))
        current = active.accept_work_items((item,), invocation_key=str(identity.invocation_key))
    assert store.compare_and_set(active, current)
    stale = replace(_final(identity), expected_state_fingerprint=active.fingerprint)
    with pytest.raises(PublicationConflictError, match="conversation state is stale"):
        publication.select_final_response(stale)
    with pool.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM dialogpilot_app.response_deliveries").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM dialogpilot_app.conversation_turns WHERE role='assistant'").fetchone()[0] == 0

    command = replace(stale, response_text="The previous lookup is recorded; no further action was taken.",
                      expected_state_fingerprint=current.fingerprint)
    result = publication.select_final_response(command)
    assert result.status is PublicationApplyStatus.APPLIED
    if transition != "independent":
        assert not current.accepts(original.control)
    changed = current.accept_work_items((_item("later", 1, work_item_id="later"),),
                                       invocation_key=str(identity.invocation_key))
    assert store.compare_and_set(current, changed)
    replay = publication.select_final_response(command)
    assert replay.status is PublicationApplyStatus.ALREADY_APPLIED
    assert replay.record.publication_id == result.record.publication_id
    assert publication.select_final_response(replace(command, response_text="changed")).status is PublicationApplyStatus.IDEMPOTENCY_CONFLICT


def test_pure_reply_does_not_bypass_freshness_with_no_work_items(publication_components):
    pool, identity, publication, _ = publication_components
    store = PostgresConversationStateStore(pool)
    empty = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    command = replace(_final(identity), expected_state_fingerprint=empty.fingerprint)
    changed = empty.accept_work_items((_item("new", 1, work_item_id="new"),),
                                     invocation_key=str(identity.invocation_key))
    assert store.compare_and_set(empty, changed)
    with pytest.raises(PublicationConflictError, match="conversation state is stale"):
        publication.select_final_response(command)


@pytest.mark.parametrize("kind", ["FIELDS", "APPROVAL", "COMPOUND"])
@pytest.mark.parametrize("change", ["unknown", "version", "consumed", "duplicate"])
def test_interaction_requires_actual_pending_signal_even_with_current_fingerprint(
    publication_components, kind, change,
):
    from tests.test_postgres_publication import _interaction
    pool, identity, publication, _ = publication_components
    command = _interaction(identity, pool)
    if kind == "FIELDS":
        command = replace(command, signal_id="fields-signal", signal_version=3)
    elif kind == "COMPOUND":
        command = replace(command, related_signals=(("fields-signal", 3),))
    command = replace(command, resume_schema={"interaction_kind": kind})
    if change == "unknown":
        command = replace(command, signal_id="never-issued")
    elif change == "version":
        command = replace(command, signal_version=command.signal_version + 1)
    elif change == "duplicate":
        command = replace(command, related_signals=((command.signal_id, command.signal_version),))
    else:
        store = PostgresConversationStateStore(pool)
        before = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
        after = replace(before, version=before.version + 1,
            pending_interaction=None if kind == "FIELDS" else before.pending_interaction,
            pending_approval=None if kind != "FIELDS" else before.pending_approval,
            consumed_signal_ids=(*before.consumed_signal_ids, command.signal_id))
        assert store.compare_and_set(before, after)
        command = replace(command, expected_state_fingerprint=after.fingerprint)
    with pytest.raises(PublicationConflictError, match="not a current pending interaction"):
        publication.publish_interaction_request(command)
    with pool.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM dialogpilot_app.response_deliveries").fetchone()[0] == 0


def test_committed_interaction_replays_after_signal_consumption(publication_components):
    from tests.test_postgres_publication import _interaction
    pool, identity, publication, _ = publication_components
    command = _interaction(identity, pool)
    first = publication.publish_interaction_request(command)
    store = PostgresConversationStateStore(pool)
    before = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    after = replace(before, version=before.version + 1, pending_approval=None,
                    consumed_signal_ids=(*before.consumed_signal_ids, command.signal_id))
    assert store.compare_and_set(before, after)
    replay = publication.publish_interaction_request(command)
    assert replay.status is PublicationApplyStatus.ALREADY_APPLIED
    assert replay.record.publication_id == first.record.publication_id


@pytest.mark.parametrize("kind", [None, "FIELDS", "COMPOUND", "UNKNOWN"])
def test_valid_approval_signal_cannot_be_published_as_another_interaction_kind(publication_components, kind):
    from tests.test_postgres_publication import _interaction
    pool, identity, publication, _ = publication_components
    command = replace(_interaction(identity, pool), resume_schema={"interaction_kind": kind})
    with pytest.raises(PublicationConflictError, match="interaction kind differs"):
        publication.publish_interaction_request(command)


@pytest.mark.parametrize("kind", ["FIELDS", "APPROVAL", "COMPOUND"])
def test_persisted_wait_with_inactive_target_cannot_request_continuation(publication_components, kind):
    from tests.test_postgres_publication import _interaction
    pool, identity, publication, _ = publication_components
    command = _interaction(identity, pool)
    if kind == "FIELDS":
        command = replace(command, signal_id="fields-signal", signal_version=3)
    elif kind == "COMPOUND":
        command = replace(command, related_signals=(("fields-signal", 3),))
    store = PostgresConversationStateStore(pool)
    state = store.load(identity.tenant_id, identity.user_id, identity.conversation_id)
    target = "input-control" if kind == "FIELDS" else "approval-control"
    # Simulate an invalid persisted pending envelope: the signal exists, but its
    # execution authority has ended. Fingerprint equality alone must not grant it.
    invalid = replace(state, version=state.version + 1, work_controls=tuple(
        replace(item, status=WorkControlStatus.CANCELLED) if item.control_id == target else item
        for item in state.work_controls))
    assert store.compare_and_set(state, invalid)
    command = replace(command, expected_state_fingerprint=invalid.fingerprint,
                      resume_schema={"interaction_kind": kind})
    with pytest.raises(PublicationConflictError, match="interaction work control is stale"):
        publication.publish_interaction_request(command)
