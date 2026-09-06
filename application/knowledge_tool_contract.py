"""Knowledge tool's domain outcomes; transport completion is not evidence."""
from __future__ import annotations

from collections.abc import Mapping
from application.agent_result import AgentResultStatus


def knowledge_outcome(data):
    if not isinstance(data, Mapping):
        return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"
    status = data.get("status")
    if not isinstance(status, str):
        return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"
    if status == "OK":
        try:
            evidence_items(data)
        except (ValueError, KeyError, TypeError):
            return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_EVIDENCE"
        return AgentResultStatus.SUCCEEDED, "KNOWLEDGE_EVIDENCE_AVAILABLE"
    if data.get("evidence_pack") is not None:
        return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"
    if status in {"NO_EVIDENCE", "AMBIGUOUS"}:
        return AgentResultStatus.BLOCKED, f"KNOWLEDGE_{status}"
    if status == "UNAVAILABLE":
        return AgentResultStatus.RETRYABLE_FAILURE, "KNOWLEDGE_UNAVAILABLE"
    if status in {"INVALID_CONTRACT", "CONFLICT"}:
        return AgentResultStatus.TERMINAL_FAILURE, f"KNOWLEDGE_{status}"
    return AgentResultStatus.TERMINAL_FAILURE, "KNOWLEDGE_INVALID_CONTRACT"


def evidence_items(data):
    """Validate the wire evidence boundary without inventing source facts."""
    pack = data["evidence_pack"]
    if data.get("status") != "OK" or not isinstance(pack, Mapping):
        raise ValueError("valid evidence pack required")
    if not pack.get("query") or not pack.get("index_manifest_fingerprint"):
        raise ValueError("evidence identity required")
    items = pack["items"]
    if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= 20:
        raise ValueError("bounded evidence required")
    ids = set()
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("evidence item must be an object")
        ref = item["source_ref"]
        if not isinstance(ref, Mapping) or not isinstance(item.get("text"), str):
            raise ValueError("evidence text and reference required")
        if not isinstance(item.get("chunk_id"), str):
            raise ValueError("evidence ID must be text")
        if any(isinstance(ref.get(k), bool) or not isinstance(ref.get(k), int) for k in ("start_char", "end_char")):
            raise ValueError("source offsets must be integers")
        if not item["chunk_id"] or item["chunk_id"] in ids:
            raise ValueError("unique evidence IDs required")
        ids.add(item["chunk_id"])
        if not item["text"].strip() or ref["end_char"] - ref["start_char"] != len(item["text"]):
            raise ValueError("evidence source interval mismatch")
        if ref["start_char"] < 0 or ref["scope"] != "public":
            raise ValueError("unsupported evidence scope")
        if not ref["source_id"] or not ref["source_revision"] or len(ref["checksum"]) != 64:
            raise ValueError("source provenance required")
    return tuple(items)


def tool_domain_outcome(result):
    if result.authority == "knowledge.active_source" and result.success:
        return knowledge_outcome(result.data)
    return None
