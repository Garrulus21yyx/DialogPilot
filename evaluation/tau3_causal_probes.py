"""Deterministic causal probes over the tau3 causal-event trace contract."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


REQUIRED_EVENT_FIELDS = ("sequence", "event_type", "task_id", "turn_id", "owner")
LINEAGE_FIELDS = (
    "goal_revision_id", "work_item_id", "proposal_id", "approval_id",
    "tool_call_id", "receipt_id",
)


def probe_report(report: Mapping[str, Any], run_dir: Path) -> Mapping[str, Any]:
    """Run causal probes and promote only owner-bound, linked evidence."""
    enriched = deepcopy(report)
    for task in enriched.get("tasks", []):
        result = _load_result(run_dir, task["result_file"])
        events = task.get("causal_events") or result.get("causal_events") or []
        probes = _validate_events(events, task["task_id"])
        if probes["contract_status"] == "VALID":
            probes["results"] = [
                _goal_revision_probe(events),
                _approval_execution_probe(events),
            ]
            for probe in probes["results"]:
                finding = probe.get("verified_root_cause")
                if finding:
                    task["findings"].append(finding)
                    task["root_cause_status"] = "VERIFIED"
        else:
            probes["results"] = [{
                "probe": "causal_lineage",
                "status": "INCONCLUSIVE",
                "reason": "The artifact does not contain a valid causal_events lineage.",
                "missing_evidence": probes["errors"],
            }]
        task["causal_probes"] = probes
    enriched["schema_version"] = "dialogpilot-tau3-rca-v2"
    return enriched


def _validate_events(events: Any, task_id: str) -> Mapping[str, Any]:
    errors = []
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)) or not events:
        errors.append("causal_events is absent or empty")
        return {"contract_status": "MISSING", "event_count": 0, "errors": errors}
    previous = -1
    seen_event_ids = set()
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            errors.append(f"event[{index}] is not an object")
            continue
        missing = [field for field in REQUIRED_EVENT_FIELDS if event.get(field) in (None, "")]
        if missing:
            errors.append(f"event[{index}] missing {','.join(missing)}")
        if str(event.get("task_id")) != str(task_id):
            errors.append(f"event[{index}] task_id does not match result")
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence <= previous:
            errors.append(f"event[{index}] sequence is not strictly increasing")
        elif isinstance(sequence, int):
            previous = sequence
        event_id = event.get("event_id")
        if event_id:
            if event_id in seen_event_ids:
                errors.append(f"event[{index}] duplicates event_id {event_id}")
            seen_event_ids.add(event_id)
    return {
        "contract_status": "INVALID" if errors else "VALID",
        "event_count": len(events),
        "lineage_coverage": {
            field: sum(1 for event in events if isinstance(event, Mapping) and event.get(field))
            for field in LINEAGE_FIELDS
        },
        "errors": errors,
    }


def _goal_revision_probe(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    active_revision = None
    for event in events:
        if event["event_type"] == "GOAL_REVISED":
            active_revision = event.get("goal_revision_id")
            continue
        if active_revision and event["event_type"] in {
            "WORK_ITEM_RESUMED", "ACTOR_DECISION", "OUTCOME_REVIEWED", "PROPOSAL_CREATED",
        }:
            consumed = event.get("goal_revision_id")
            if consumed and consumed != active_revision:
                if event.get("outcome_impact") != "TASK_BLOCKING":
                    return {
                        "probe": "goal_revision_propagation",
                        "status": "INCONCLUSIVE",
                        "expected_revision": active_revision,
                        "consumed_revision": consumed,
                        "event_id": event.get("event_id"),
                        "reason": "A stale revision was consumed, but terminal outcome impact is not linked.",
                    }
                return {
                    "probe": "goal_revision_propagation",
                    "status": "FAIL",
                    "expected_revision": active_revision,
                    "consumed_revision": consumed,
                    "event_id": event.get("event_id"),
                    "verified_root_cause": _root_finding(
                        "STALE_GOAL_REVISION_CONSUMED", event,
                        "A downstream owner consumed an older goal revision after GOAL_REVISED.",
                    ),
                }
            if not consumed:
                return {
                    "probe": "goal_revision_propagation", "status": "INCONCLUSIVE",
                    "reason": "A downstream goal consumer omitted goal_revision_id.",
                    "event_id": event.get("event_id"),
                }
    return {
        "probe": "goal_revision_propagation",
        "status": "PASS" if active_revision else "NOT_APPLICABLE",
    }


def _approval_execution_probe(events: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    approvals = [event for event in events if event["event_type"] == "ACTION_APPROVED"]
    for approval in approvals:
        proposal_id = approval.get("proposal_id")
        if not proposal_id:
            return {
                "probe": "approval_to_execution", "status": "INCONCLUSIVE",
                "reason": "ACTION_APPROVED omitted proposal_id.",
                "event_id": approval.get("event_id"),
            }
        downstream = [
            event for event in events
            if event["sequence"] > approval["sequence"] and event.get("proposal_id") == proposal_id
        ]
        if any(event["event_type"] == "TOOL_COMMITTED" for event in downstream):
            continue
        failure = next((event for event in downstream if event["event_type"] == "EXECUTION_FAILED"), None)
        if failure and failure.get("reason_code") and failure.get("outcome_impact") == "TASK_BLOCKING":
            return {
                "probe": "approval_to_execution", "status": "FAIL",
                "proposal_id": proposal_id,
                "event_id": failure.get("event_id"),
                "verified_root_cause": _root_finding(
                    "APPROVED_ACTION_EXECUTION_FAILED", failure,
                    f"The approved proposal failed before commit with {failure['reason_code']}.",
                ),
            }
        if failure and failure.get("reason_code"):
            return {
                "probe": "approval_to_execution", "status": "INCONCLUSIVE",
                "proposal_id": proposal_id,
                "event_id": failure.get("event_id"),
                "reason": "A linked execution failure exists, but terminal outcome impact is not linked.",
            }
        return {
            "probe": "approval_to_execution", "status": "INCONCLUSIVE",
            "proposal_id": proposal_id,
            "reason": "No linked TOOL_COMMITTED or typed EXECUTION_FAILED event follows approval.",
        }
    return {"probe": "approval_to_execution", "status": "NOT_APPLICABLE"}


def _root_finding(code: str, event: Mapping[str, Any], summary: str) -> Mapping[str, Any]:
    return {
        "code": code,
        "layer": "root_cause",
        "level": "VERIFIED",
        "summary": summary,
        "owner_candidate": event.get("owner"),
        "evidence_ids": [event.get("event_id") or f"causal-event-{event['sequence']}"],
        "missing_evidence": [],
        "next_probe": None,
        "impact": "TASK_BLOCKING",
    }


def _load_result(run_dir: Path, filename: str) -> Mapping[str, Any]:
    return json.loads((run_dir / filename).read_text())
