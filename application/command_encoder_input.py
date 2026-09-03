"""Stable input renderer for the selective command Encoder."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping

from application.turn_state import TurnStateSnapshot


RENDERER_VERSION = "command-encoder-state-renderer-v1"
_RENDERER_SPEC = {
    "message": "trimmed verbatim text",
    "history": "ordered role and trimmed content",
    "state": (
        "sorted active flow definition refs, pending slot field, active case count, "
        "reusable media count"
    ),
}
RENDERER_SHA256 = hashlib.sha256(
    json.dumps(
        _RENDERER_SPEC,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def render_command_encoder_input(
    message: str,
    state: TurnStateSnapshot,
    *,
    history: tuple[Mapping[str, str], ...] = (),
) -> str:
    """Render only the bounded state available to the selective Encoder."""
    if not isinstance(message, str) or not message.strip():
        raise ValueError("encoder message is required")
    payload = {
        "message": message.strip(),
        "history": [
            {
                "role": str(item.get("role") or "unknown").strip(),
                "content": str(item.get("content") or "").strip(),
            }
            for item in history
        ],
        "state": {
            "active_flows": sorted(
                f"{item.definition.flow_id}@{item.definition.version}"
                for item in state.active_flows
            ),
            "pending_slot": (
                state.pending_slot.field_name if state.pending_slot else None
            ),
            "active_case_count": len(state.active_case_refs),
            "reusable_media_count": len(state.reusable_media_refs),
        },
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
