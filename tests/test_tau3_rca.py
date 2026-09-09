"""The tau3 RCA analyzer separates facts, hypotheses, and unbound evidence."""
from __future__ import annotations

import json
from pathlib import Path

from evaluation.tau3_rca import analyze_run, analyze_task


def _write(path: Path, value) -> Path:
    path.write_text(json.dumps(value))
    return path


def _failed_write(name: str, arguments: dict) -> dict:
    return {
        "action": {"name": name, "arguments": arguments},
        "action_match": False,
        "tool_type": "write",
    }


def test_write_argument_mismatch_keeps_simulator_drift_as_hypothesis(tmp_path):
    result = _write(tmp_path / "task-13.json", {
        "task_id": "13", "official_reward": 0,
        "action": {"reward": 0, "action_checks": [
            _failed_write("return_delivered_order_items", {"item_ids": ["a", "b"]})
        ]},
    })
    trajectory = _write(tmp_path / "task-13-trajectory.json", {"messages": [
        {"role": "assistant", "content": "Return a, b and keyboard?"},
        {"role": "user", "content": "Yes, go ahead with all four."},
        {"role": "assistant", "tool_calls": [{
            "id": "call-1", "name": "return_delivered_order_items",
            "arguments": {"item_ids": ["a", "b", "keyboard"]},
        }]},
    ]})

    report = analyze_task(result, trajectory)

    findings = {item["code"]: item for item in report["findings"]}
    assert findings["WRITE_ARGUMENT_MISMATCH"]["level"] == "VERIFIED"
    assert findings["SIMULATOR_GOAL_DRIFT"]["level"] == "SUPPORTED"
    assert findings["SIMULATOR_GOAL_DRIFT"]["missing_evidence"]
    assert report["root_cause_status"] == "OPEN"
    mismatch = next(item for item in report["evidence"] if item["evidence_id"] == "expected-write-0")
    assert mismatch["data"]["diff"]["item_ids"]["actual"] == ["a", "b", "keyboard"]


def test_context_failure_is_bound_to_task_trace_and_missing_write(tmp_path):
    result = _write(tmp_path / "task-20.json", {
        "task_id": "20", "official_reward": 0,
        "action": {"reward": 0, "action_checks": [
            _failed_write("modify_pending_order_items", {"order_id": "W1"})
        ]},
        "target_trace": [{"reason_code": "CONTEXT_BUDGET_EXCEEDED"}],
    })
    trajectory = _write(tmp_path / "task-20-trajectory.json", {"messages": []})

    report = analyze_task(result, trajectory)

    codes = {item["code"] for item in report["findings"]}
    assert {"MISSING_REQUIRED_WRITE", "CONTEXT_BUDGET_EXECUTION_FAILURE"} <= codes
    assert report["root_cause_status"] == "OPEN"


def test_recovered_context_error_is_not_reported_as_task_blocking(tmp_path):
    result = _write(tmp_path / "task-19.json", {
        "task_id": "19", "official_reward": None,
        "env": {"reward": 1}, "action": {"reward": 1, "action_checks": []},
        "evaluation_errors": {"all": {"stage": "evaluation"}},
        "target_trace": [{"reason_code": "CONTEXT_BUDGET_EXCEEDED"}],
    })

    report = analyze_task(result)

    findings = {item["code"]: item for item in report["findings"]}
    assert findings["EVALUATOR_DEPENDENCY_FAILURE"]["impact"] == "EVALUATION_ONLY"
    assert findings["CONTEXT_BUDGET_EXECUTION_FAILURE"]["impact"] == "RECOVERED"


def test_scope_revision_is_supported_but_does_not_claim_owner(tmp_path):
    result = _write(tmp_path / "task-8.json", {
        "task_id": "8", "official_reward": 0,
        "action": {"reward": 0, "action_checks": [
            _failed_write("exchange_delivered_order_items", {"item_ids": ["lamp"]})
        ]},
        "target_trace": [{"reason_code": "DOMAIN_OUTCOME_REJECTED"}],
    })
    trajectory = _write(tmp_path / "task-8-trajectory.json", {"messages": [
        {"role": "user", "content": "Only exchange the lamp; skip the bottle."},
        {"role": "assistant", "content": "I still need both."},
    ]})

    report = analyze_task(result, trajectory)

    finding = next(item for item in report["findings"] if item["code"] == "GOAL_REVISION_NOT_APPLIED")
    assert finding["level"] == "SUPPORTED"
    assert finding["owner_candidate"] == "goal_state_transition"
    assert "reviewer" in finding["missing_evidence"][0]


def test_run_does_not_assign_unkeyed_batch_errors_to_tasks(tmp_path):
    _write(tmp_path / "manifest.json", {"status": "INCOMPLETE", "project_commit": "abc"})
    _write(tmp_path / "task-1.json", {
        "task_id": "1", "official_reward": 1,
        "env": {"reward": 1}, "action": {"reward": 1, "action_checks": []},
    })
    (tmp_path / "application-errors.log").write_text("CONTEXT_BUDGET_EXCEEDED\n")

    report = analyze_run(tmp_path)

    assert report["tasks"][0]["findings"][0]["code"] == "PASS"
    assert report["unbound_run_evidence"] == [{
        "code": "CONTEXT_BUDGET_EXCEEDED", "count": 1,
        "source": "application-errors.log", "binding": "RUN_ONLY",
        "note": "Not attributed to a task because the log line lacks a stable task/turn/trace join key.",
    }]
