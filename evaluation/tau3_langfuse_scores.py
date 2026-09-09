"""Optional publication of compact tau3 RCA scores to Langfuse sessions."""
from __future__ import annotations

from datetime import datetime
import hashlib
from typing import Any, Mapping


def publish_scores(report: Mapping[str, Any], client=None) -> Mapping[str, Any]:
    """Publish idempotent session scores; no credentials are needed for dry runs."""
    if client is None:
        from langfuse import Langfuse
        client = Langfuse()
    published = []
    skipped = []
    run = report.get("run", {})
    timestamp = _timestamp(run.get("finished_at") or run.get("started_at"))
    for task in report.get("tasks", []):
        session_id = task.get("langfuse_session_id")
        if not session_id:
            skipped.append({"task_id": task["task_id"], "reason": "missing langfuse_session_id"})
            continue
        blocking = sorted({
            item["code"] for item in task.get("findings", [])
            if item.get("impact") in {"TASK_BLOCKING", "OUTCOME_DEVIATION", "RUN_BLOCKING"}
        })
        env_reward = task.get("outcome", {}).get("env_reward")
        scores = [
            ("tau3_run_available", 0.0 if any(
                item.get("impact") == "RUN_BLOCKING" for item in task.get("findings", [])
            ) else 1.0, "BOOLEAN"),
            ("tau3_rca_status", task.get("root_cause_status", "OPEN"), "CATEGORICAL"),
            ("tau3_failure_codes", ",".join(blocking)[:500] or "none", "TEXT"),
        ]
        if env_reward is not None:
            scores.insert(0, ("tau3_business_pass", 1.0 if float(env_reward) == 1.0 else 0.0, "BOOLEAN"))
        else:
            skipped.append({"task_id": task["task_id"], "reason": "business score unavailable"})
        for name, value, data_type in scores:
            score_id = hashlib.sha256(f"tau3-rca-v2:{session_id}:{name}".encode()).hexdigest()[:32]
            client.create_score(
                name=name, value=value, session_id=session_id, score_id=score_id,
                data_type=data_type, timestamp=timestamp,
                comment=f"Automated tau3 RCA for task {task['task_id']}",
                metadata={"task_id": str(task["task_id"]), "schema_version": report.get("schema_version")},
            )
            published.append({"task_id": task["task_id"], "session_id": session_id, "name": name})
    flush = getattr(client, "flush", None)
    if callable(flush):
        flush()
    return {"published": published, "skipped": skipped}


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
