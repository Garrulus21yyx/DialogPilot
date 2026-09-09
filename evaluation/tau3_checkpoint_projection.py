"""Sanitized evaluation projection of the existing LangGraph checkpoints."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Mapping


_LINEAGE_FIELDS = frozenset({
    "action_name", "allowed_tools", "approval_id", "checkpoint_thread_id",
    "committed_receipt_refs", "control_id", "control_mode", "invocation_key",
    "operation_key", "plan_id", "proposal_id", "reason_code", "receipt_id",
    "receipt_ref", "requirement_id", "revision", "signal_id", "status",
    "tool_call_id", "workflow_run_id", "work_item_id",
})
_SKIP_BRANCHES = frozenset({
    "business_observations", "execution_feedback", "historical_context_view",
    "presentation_state", "recent_relevant_turns", "state_before", "working_messages",
})


async def project_session_checkpoints(
    checkpointer: Any, *, session_id: str, task_id: str,
) -> Mapping[str, Any]:
    """Project join keys before the temporary tau3 checkpoint database is removed."""
    snapshots = []
    try:
        async for item in checkpointer.alist(
            None, filter={"langfuse_session_id": session_id}, limit=None,
        ):
            config = item.config.get("configurable", {})
            checkpoint = item.checkpoint
            snapshots.append({
                "thread_id": config.get("thread_id"),
                "checkpoint_ns": config.get("checkpoint_ns", ""),
                "checkpoint_id": config.get("checkpoint_id"),
                "parent_checkpoint_id": (
                    (item.parent_config or {}).get("configurable", {}).get("checkpoint_id")
                ),
                "timestamp": checkpoint.get("ts"),
                "step": item.metadata.get("step"),
                "source": item.metadata.get("source"),
                "lineage": _collect_lineage(checkpoint.get("channel_values", {})),
            })
    except Exception as exc:
        return {
            "schema_version": "tau3-checkpoint-projection-v1",
            "status": "ERROR",
            "task_id": str(task_id),
            "session_id": session_id,
            "snapshot_count": 0,
            "error_type": type(exc).__name__,
        }
    snapshots.sort(key=lambda item: (
        str(item.get("timestamp") or ""), str(item.get("thread_id") or ""),
        str(item.get("checkpoint_id") or ""),
    ))
    lineage_by_checkpoint = {}
    for sequence, snapshot in enumerate(snapshots, 1):
        snapshot["sequence"] = sequence
        current = {item["path"]: item for item in snapshot["lineage"]}
        parent = lineage_by_checkpoint.get(snapshot.get("parent_checkpoint_id"), {})
        snapshot["lineage"] = [
            item for path, item in current.items()
            if path not in parent or parent[path]["value"] != item["value"]
        ]
        removed = sorted(set(parent).difference(current))
        if removed:
            snapshot["removed_lineage_paths"] = removed[:2000]
        checkpoint_id = snapshot.get("checkpoint_id")
        if checkpoint_id:
            lineage_by_checkpoint[checkpoint_id] = current
    return {
        "schema_version": "tau3-checkpoint-projection-v1",
        "status": "AVAILABLE" if snapshots else "EMPTY",
        "task_id": str(task_id),
        "session_id": session_id,
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
    }


def _collect_lineage(value: Any) -> list[Mapping[str, Any]]:
    found = []
    seen: set[int] = set()

    def visit(current: Any, path: str, depth: int) -> None:
        if depth > 12 or len(found) >= 2000:
            return
        if isinstance(current, (Mapping, list, tuple)) or is_dataclass(current):
            identity = id(current)
            if identity in seen:
                return
            seen.add(identity)
        if is_dataclass(current):
            entries = ((field.name, getattr(current, field.name)) for field in fields(current))
        elif isinstance(current, Mapping):
            entries = current.items()
        elif isinstance(current, (list, tuple)):
            for index, child in enumerate(current[:200]):
                visit(child, f"{path}[{index}]", depth + 1)
            return
        else:
            return
        for raw_key, child in entries:
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key in _LINEAGE_FIELDS:
                projected = _project_value(child)
                if projected is not None:
                    found.append({"path": child_path, "field": key, "value": projected})
            if key in _SKIP_BRANCHES:
                continue
            visit(child, child_path, depth + 1)

    visit(value, "$.channel_values", 0)
    return found


def _project_value(value: Any) -> Any:
    if isinstance(value, Enum):
        value = value.value
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)) and len(value) <= 50:
        projected = [_project_value(item) for item in value]
        if all(item is not None and not isinstance(item, (dict, list)) for item in projected):
            return projected
    return None
