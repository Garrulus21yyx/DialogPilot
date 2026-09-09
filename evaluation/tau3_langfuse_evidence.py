"""Fetch compact, join-keyed RCA evidence from Langfuse sessions."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


def enrich_from_langfuse(report: Mapping[str, Any], client=None) -> Mapping[str, Any]:
    """Fetch session traces and extract causal events from standardized metadata."""
    if client is None:
        from langfuse import Langfuse
        client = Langfuse()
    enriched = deepcopy(report)
    for task in enriched.get("tasks", []):
        session_id = task.get("langfuse_session_id")
        if not session_id:
            task["langfuse_evidence"] = {"status": "UNAVAILABLE", "reason": "missing langfuse_session_id"}
            continue
        traces = _session_traces(client, session_id)
        observations = []
        for trace in traces:
            observations.extend(_trace_observations(client, str(_value(trace, "id"))))
        causal_events = []
        errors = []
        for observation in observations:
            metadata = _value(observation, "metadata") or {}
            if not isinstance(metadata, Mapping):
                metadata = {}
            event = _causal_event(metadata)
            if event:
                causal_events.append(event)
            level = str(_value(observation, "level") or "").upper()
            if level == "ERROR" or _value(observation, "status_message"):
                errors.append({
                    "observation_id": _value(observation, "id"),
                    "trace_id": _value(observation, "trace_id") or _value(observation, "traceId"),
                    "name": _value(observation, "name"),
                    "level": level,
                    "status_message": str(_value(observation, "status_message") or "")[:240],
                    "join_keys": _join_keys(metadata),
                })
        causal_events.sort(key=lambda item: item["sequence"])
        task["causal_events"] = causal_events
        task["langfuse_evidence"] = {
            "status": "FETCHED",
            "session_id": session_id,
            "trace_ids": [str(_value(trace, "id")) for trace in traces],
            "observation_count": len(observations),
            "causal_event_count": len(causal_events),
            "errors": errors,
        }
    return enriched


def _session_traces(client, session_id: str) -> list[Any]:
    traces = []
    page = 1
    while True:
        response = client.api.trace.list(session_id=session_id, page=page, limit=100)
        batch = list(_value(response, "data") or [])
        traces.extend(batch)
        if len(batch) < 100:
            return traces
        page += 1


def _trace_observations(client, trace_id: str) -> list[Any]:
    observations = []
    cursor = None
    while True:
        kwargs = {"trace_id": trace_id, "limit": 100}
        if cursor:
            kwargs["cursor"] = cursor
        response = client.api.observations.get_many(**kwargs)
        observations.extend(list(_value(response, "data") or []))
        meta = _value(response, "meta")
        cursor = _value(meta, "cursor") if meta else None
        if not cursor:
            return observations


def _causal_event(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    event_type = metadata.get("causal.event_type")
    sequence = metadata.get("causal.sequence")
    if not event_type or sequence in (None, ""):
        return None
    try:
        sequence = int(sequence)
    except (TypeError, ValueError):
        return None
    event = {
        "event_id": metadata.get("causal.event_id"),
        "sequence": sequence,
        "event_type": event_type,
        "task_id": metadata.get("causal.task_id"),
        "turn_id": metadata.get("causal.turn_id"),
        "owner": metadata.get("causal.owner"),
        "outcome_impact": metadata.get("causal.outcome_impact"),
        "reason_code": metadata.get("causal.reason_code"),
    }
    for field in (
        "goal_revision_id", "work_item_id", "proposal_id", "approval_id",
        "tool_call_id", "receipt_id",
    ):
        event[field] = metadata.get(f"causal.{field}")
    return {key: value for key, value in event.items() if value not in (None, "")}


def _join_keys(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: value for key, value in metadata.items()
        if str(key).startswith(("causal.", "dialogpilot."))
    }


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
