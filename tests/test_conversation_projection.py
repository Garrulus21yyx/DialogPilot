"""Bounded projection policy algebra for conversation event classes."""
import pytest

from application.conversation_projection import (
    ConversationProjectionPolicyV1,
    ConversationSubject,
    ProjectableConversationEvent,
    ProjectionName,
)


def _event(
    projection: ProjectionName,
    *,
    event_type="FINAL_RESPONSE_SELECTED",
    disposition="normal",
):
    return ProjectableConversationEvent(
        outbox_id="outbox",
        projection_name=projection,
        generation=1,
        event_id="event",
        event_seq=1,
        event_type=event_type,
        payload={"projection_disposition": disposition},
        source_deletion_epoch=0,
        subject=ConversationSubject("tenant", "user", "conversation"),
        attempt=1,
    )


@pytest.mark.parametrize("event_type,disposition", [
    ("FINAL_RESPONSE_SELECTED", "out_of_scope"),
    ("FINAL_RESPONSE_SELECTED", "clarification"),
    ("INTERACTION_REQUEST_PUBLISHED", "approval"),
    ("RESUME_REJECTED", "normal"),
])
def test_non_factual_events_only_enter_working_and_summary(
    event_type, disposition,
):
    policy = ConversationProjectionPolicyV1()
    assert policy.allows(_event(
        ProjectionName.WORKING_WINDOW,
        event_type=event_type,
        disposition=disposition,
    ))
    assert policy.allows(_event(
        ProjectionName.THREAD_SUMMARY,
        event_type=event_type,
        disposition=disposition,
    ))
    assert not policy.allows(_event(
        ProjectionName.EPISODIC_INDEX,
        event_type=event_type,
        disposition=disposition,
    ))
    assert not policy.allows(_event(
        ProjectionName.FACT_EXTRACTION,
        event_type=event_type,
        disposition=disposition,
    ))


@pytest.mark.parametrize("event_type", [
    "REQUEST_ACCEPTED",
    "RESUME_REQUESTED",
    "FINAL_RESPONSE_SELECTED",
    "HUMAN_REPLY_PUBLISHED",
    "LEGACY_FINAL_RESPONSE_IMPORTED",
])
def test_normal_events_follow_closed_target_specific_projection_policy(
    event_type,
):
    policy = ConversationProjectionPolicyV1()
    allowed = {
        projection
        for projection in ProjectionName
        if policy.allows(_event(projection, event_type=event_type))
    }
    expected = set(ProjectionName)
    if event_type not in {
        "FINAL_RESPONSE_SELECTED", "LEGACY_FINAL_RESPONSE_IMPORTED",
    }:
        expected.remove(ProjectionName.FACT_EXTRACTION)
    assert allowed == expected


def test_deletion_event_always_reaches_every_projection_for_cleanup():
    policy = ConversationProjectionPolicyV1()
    assert all(policy.allows(_event(
        projection,
        event_type="CONVERSATION_DELETED",
        disposition="out_of_scope",
    )) for projection in ProjectionName)
