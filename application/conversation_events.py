"""Public, replayable conversation event contract."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PublicConversationEvent:
    event_id: str
    seq: int
    event_type: str
    conversation_id: str
    invocation_key: str | None
    created_at: str
    payload: Mapping[str, Any]

    def to_sse(self) -> str:
        body = json.dumps(
            {
                "seq": self.seq,
                "conversation_id": self.conversation_id,
                "invocation_key": self.invocation_key,
                "created_at": self.created_at,
                **dict(self.payload),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"id: {self.event_id}\nevent: {self.event_type}\ndata: {body}\n\n"


@dataclass(frozen=True)
class PublicEventPage:
    events: tuple[PublicConversationEvent, ...]
    last_event_id: str | None
    reset_required: bool = False


def reset_event(conversation_id: str) -> str:
    body = json.dumps({
        "reason": "cursor_unavailable",
        "conversation_id": conversation_id,
        "recover_with": {
            "transcript": f"/conversations/{conversation_id}/turns",
            "instruction": "reload transcript then reconnect without cursor",
        },
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"event: stream.reset\ndata: {body}\n\n"
