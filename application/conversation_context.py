"""Shared source-preserving projection of the loaded conversation context."""

def conversation_context_payload(turn_context):
    from application.knowledge_tool_contract import evidence_id, evidence_items
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
        **({"knowledge_evidence": list(turn_context.knowledge_evidence),
        "knowledge_evidence_labels": {
            item["chunk_id"]: evidence_id(item["chunk_id"])
            for entry in turn_context.knowledge_evidence if entry.get("status") == "CURRENT"
            for item in evidence_items(entry["pack"])
        }, "knowledge_reuse_contract": (
            "These are sources used by prior replies, not new retrieval results. CURRENT means "
            "the source is still authorized and effective at checked_at, not that it covers every new question. "
            "Reuse it for covered follow-ups without another lookup. Preserve its original query, scope and "
            "observed_at. Search only for missing coverage, changed applicability or an explicit refresh request. "
            "NOT_REUSABLE entries supply no current source support; ordinary conversation may still continue. "
            "History without an attached source is conversation, not proof that a new policy conclusion is true or false."
        )} if turn_context.knowledge_evidence else {}),
    }
