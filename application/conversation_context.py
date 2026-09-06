"""Shared source-preserving projection of the loaded conversation context."""

def conversation_context_payload(turn_context):
    if turn_context is None:
        return {
            "projection_status": "UNAVAILABLE",
            "source_watermark": 0,
            "reason_codes": ["CONTEXT_NOT_PROVIDED"],
            "summary": None,
            "recent_messages": [],
            "evidence_refs": [],
        }
    summary = turn_context.summary
    return {
        "projection_status": turn_context.projection_status.value,
        "source_watermark": turn_context.source_watermark,
        "reason_codes": list(turn_context.projection_reason_codes),
        "summary": (
            {
                "content": summary.content,
                "source_ref": summary.source_ref,
                "covered_until_seq": summary.covered_until_seq,
                "producer_version": summary.producer_version,
            }
            if summary is not None else None
        ),
        "recent_messages": [
            {
                "role": item.role,
                "content": item.content,
                "source_ref": item.source_ref,
                "seq": item.seq,
                "observed_at": item.observed_at,
            }
            for item in turn_context.recent_messages
        ],
        "evidence_refs": list(turn_context.evidence_refs),
    }
