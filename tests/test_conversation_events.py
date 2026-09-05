import json

from application.conversation_events import PublicConversationEvent, reset_event


def test_public_event_serializes_as_stable_sse_frame():
    event = PublicConversationEvent(
        event_id="event-2",
        seq=2,
        event_type="response.committed",
        conversation_id="conversation-a",
        invocation_key="invocation-a",
        created_at="2026-09-05T10:00:00+00:00",
        payload={"response_id": "response-a", "response": "订单已发货。"},
    )
    frame = event.to_sse()
    lines = frame.strip().splitlines()
    assert lines[:2] == ["id: event-2", "event: response.committed"]
    payload = json.loads(lines[2].removeprefix("data: "))
    assert payload["seq"] == 2
    assert payload["response"] == "订单已发货。"
    assert event.to_sse() == frame


def test_unavailable_cursor_directs_client_to_authoritative_transcript():
    frame = reset_event("conversation-a")
    assert frame.startswith("event: stream.reset\n")
    payload = json.loads(frame.splitlines()[1].removeprefix("data: "))
    assert payload["reason"] == "cursor_unavailable"
    assert payload["recover_with"]["transcript"] == (
        "/conversations/conversation-a/turns"
    )
