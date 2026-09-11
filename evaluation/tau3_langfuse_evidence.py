"""Fetch compact, join-keyed RCA evidence from Langfuse sessions."""
from __future__ import annotations

from copy import deepcopy
import json
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
        status_events = []
        semantic_observations = []
        execution_chain = []
        for observation in observations:
            metadata = _value(observation, "metadata") or {}
            if not isinstance(metadata, Mapping):
                metadata = {}
            execution_chain.append(_observation_view(observation, metadata))
            event = _causal_event(metadata)
            if event:
                event.setdefault("task_id", str(task["task_id"]))
                causal_events.append(event)
            level = str(_value(observation, "level") or "").upper()
            status_message = str(_value(observation, "status_message") or "")[:240]
            diagnostic = {
                "observation_id": _value(observation, "id"),
                "trace_id": _value(observation, "trace_id") or _value(observation, "traceId"),
                "name": _value(observation, "name"),
                "level": level,
                "status_message": status_message,
                "join_keys": _join_keys(metadata),
            }
            if level == "ERROR":
                errors.append(diagnostic)
            elif status_message:
                status_events.append(diagnostic)
            if _semantic_observation(observation, metadata):
                semantic_observations.append({
                    "observation_id": _value(observation, "id"),
                    "trace_id": _value(observation, "trace_id") or _value(observation, "traceId"),
                    "parent_observation_id": (
                        _value(observation, "parent_observation_id")
                        or _value(observation, "parentObservationId")
                    ),
                    "name": _value(observation, "name"),
                    "start_time": str(
                        _value(observation, "start_time") or _value(observation, "startTime") or ""
                    ),
                    "level": level,
                    "input": _bounded(_value(observation, "input")),
                    "output": _bounded(_value(observation, "output")),
                    "join_keys": _join_keys(metadata),
                })
        causal_events.sort(key=lambda item: item["sequence"])
        execution_chain.sort(key=lambda item: (
            not bool(item.get("start_time")), item.get("start_time") or "",
            item.get("trace_id") or "",
            item.get("observation_id") or "",
        ))
        task["causal_events"] = causal_events
        task["langfuse_evidence"] = {
            "status": "FETCHED",
            "session_id": session_id,
            "trace_ids": [str(_value(trace, "id")) for trace in traces],
            "observation_count": len(observations),
            "causal_event_count": len(causal_events),
            "execution_chain": execution_chain,
            "token_usage": _token_summary(execution_chain),
            "compaction_usage": [
                {
                    "event_id": event.get("event_id"),
                    "work_item_id": event.get("work_item_id"),
                    "before_tokens": event.get("before_tokens"),
                    "after_tokens": event.get("after_tokens"),
                    "saved_tokens": event.get("before_tokens") - event.get("after_tokens"),
                }
                for event in causal_events
                if event.get("event_type") == "CONTEXT_COMPACTED"
                and isinstance(event.get("before_tokens"), int)
                and isinstance(event.get("after_tokens"), int)
            ],
            "errors": errors,
            "status_events": status_events,
            "semantic_observations": semantic_observations[-80:],
            "semantic_observations_truncated": max(0, len(semantic_observations) - 80),
        }
    return enriched


def _observation_view(observation: Any, metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: value
        for key, value in {
            "observation_id": _value(observation, "id"),
            "trace_id": _value(observation, "trace_id") or _value(observation, "traceId"),
            "parent_observation_id": (
                _value(observation, "parent_observation_id")
                or _value(observation, "parentObservationId")
            ),
            "type": _value(observation, "type"),
            "name": _value(observation, "name"),
            "start_time": str(
                _value(observation, "start_time") or _value(observation, "startTime") or ""
            ),
            "end_time": str(
                _value(observation, "end_time") or _value(observation, "endTime") or ""
            ),
            "level": str(_value(observation, "level") or "").upper(),
            "status_message": str(_value(observation, "status_message") or "")[:240],
            "usage": _usage_details(observation),
            "cost": _cost_details(observation),
            "join_keys": _join_keys(metadata),
        }.items()
        if value not in (None, "", {}, [])
    }


def _usage_details(observation: Any) -> Mapping[str, int] | None:
    raw = (
        _value(observation, "usage_details")
        or _value(observation, "usageDetails")
        or _value(observation, "usage")
    )
    if not isinstance(raw, Mapping):
        return None
    input_tokens = _integer(raw, "input", "input_tokens", "prompt_tokens")
    output_tokens = _integer(raw, "output", "output_tokens", "completion_tokens")
    total_tokens = _integer(raw, "total", "total_tokens")
    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens
    normalized = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    aliases = {
        "input", "input_tokens", "prompt_tokens",
        "output", "output_tokens", "completion_tokens",
        "total", "total_tokens",
    }
    for key, value in raw.items():
        if str(key) in aliases:
            continue
        try:
            normalized[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return {key: value for key, value in normalized.items() if value is not None} or None


def _cost_details(observation: Any) -> Mapping[str, float] | None:
    raw = _value(observation, "cost_details") or _value(observation, "costDetails")
    if not isinstance(raw, Mapping):
        return None
    normalized = {}
    for source, target in (("input", "input_cost"), ("output", "output_cost"), ("total", "total_cost")):
        value = raw.get(source, raw.get(target))
        try:
            normalized[target] = float(value)
        except (TypeError, ValueError):
            continue
    if "total_cost" not in normalized and {"input_cost", "output_cost"} <= normalized.keys():
        normalized["total_cost"] = normalized["input_cost"] + normalized["output_cost"]
    return normalized or None


def _integer(value: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        candidate = value.get(key)
        if candidate in (None, ""):
            continue
        try:
            return int(candidate)
        except (TypeError, ValueError):
            continue
    return None


def _token_summary(execution_chain: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    generations = [
        item for item in execution_chain
        if str(item.get("type") or "").upper() == "GENERATION" or item.get("usage")
    ]
    measured = [item for item in generations if item.get("usage")]
    if not generations:
        status = "UNAVAILABLE"
    elif len(measured) == len(generations):
        status = "COMPLETE"
    elif measured:
        status = "PARTIAL"
    else:
        status = "UNAVAILABLE"
    breakdown = {}
    for item in measured:
        for key, value in item["usage"].items():
            if key in {"input_tokens", "output_tokens", "total_tokens"}:
                continue
            breakdown[key] = breakdown.get(key, 0) + value
    return {
        "status": status,
        "generation_count": len(generations),
        "measured_generation_count": len(measured),
        "missing_generation_count": len(generations) - len(measured),
        "input_tokens": sum(item["usage"].get("input_tokens", 0) for item in measured),
        "output_tokens": sum(item["usage"].get("output_tokens", 0) for item in measured),
        "total_tokens": sum(item["usage"].get("total_tokens", 0) for item in measured),
        "additional_usage": dict(sorted(breakdown.items())),
    }


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
        kwargs = {
            "trace_id": trace_id,
            "limit": 100,
            "fields": "core,basic,time,io,metadata,model,usage,metrics",
        }
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
        "evidence_origin": metadata.get("causal.evidence_origin"),
        "reason_code": metadata.get("causal.reason_code"),
    }
    for field in (
        "control_id", "control_revision", "work_item_id", "proposal_id",
        "approval_id", "tool_call_id", "receipt_id", "action_name", "requirement_id",
        "read_identity", "guard_decision", "stagnant_rounds", "before_tokens",
        "after_tokens", "summary_applied", "source_index_present",
        "observation_read_call_id", "emitted_business_call_id", "source_business_call_ids",
        "reuse_decision", "refresh_requested", "prior_result_present",
    ):
        value = metadata.get(f"causal.{field}")
        if field in {"control_revision", "stagnant_rounds", "before_tokens", "after_tokens"} and value not in (None, ""):
            try:
                value = int(value)
            except (TypeError, ValueError):
                value = None
        if field in {"summary_applied", "source_index_present", "refresh_requested",
                     "prior_result_present"} and value not in (None, ""):
            value = str(value).casefold() == "true"
        if field == "source_business_call_ids" and value not in (None, ""):
            try:
                decoded = json.loads(value) if isinstance(value, str) else value
                value = [str(item) for item in decoded] if isinstance(decoded, list) else None
            except (TypeError, ValueError):
                value = None
        event[field] = value
    return {key: value for key, value in event.items() if value not in (None, "")}


def _join_keys(metadata: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        key: value for key, value in metadata.items()
        if str(key).startswith(("causal.", "dialogpilot."))
    }


def _semantic_observation(observation: Any, metadata: Mapping[str, Any]) -> bool:
    name = str(_value(observation, "name") or "").casefold()
    return bool(metadata.get("causal.event_type")) or any(marker in name for marker in (
        "assess_domain_outcome", "conversation", "understanding", "planner",
        "approval", "agent_execution", "compose_response", "verification",
    )) or str(_value(observation, "level") or "").upper() == "ERROR"


def _bounded(value: Any, limit: int = 4000) -> Any:
    if value is None:
        return None
    text = value if isinstance(value, str) else str(value)
    return text[:limit]


def _value(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)
