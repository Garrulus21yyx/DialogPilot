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


def test_unchanged_reads_prove_blocking_mechanism_but_not_producer_root(tmp_path):
    result = _write(tmp_path / "task-20.json", {
        "task_id": "20", "official_reward": 0,
        "termination": "max_steps", "evaluation_scope": "termination_gate_only",
        "env": {"reward": 0, "db_check": None},
        "action": {"reward": 0, "action_checks": None},
        "target_trace": [{
            "turn": 3,
            "outcome": {"kind": "APPROVAL", "signal_id": "approval-1"},
        }],
    })
    messages = [{"role": "assistant", "content": "Hello"}, {"role": "user", "content": "Start"}]
    for index in range(3):
        call_id = f"call-{index}"
        messages.extend([
            {"role": "assistant", "tool_calls": [{
                "id": call_id, "name": "get_order_details", "arguments": {"order_id": "W1"},
            }]},
            {"id": call_id, "role": "tool", "content": "same-order-state"},
        ])
    messages.extend([
        {"role": "assistant", "content": "Prepared"},
        {"role": "user", "content": "Use gift card"},
        {"role": "assistant", "content": "Please approve"},
    ])
    trajectory = _write(tmp_path / "task-20-trajectory.json", {"messages": messages})

    report = analyze_task(result, trajectory, run_context={"max_steps": 10})

    findings = {item["code"]: item for item in report["findings"]}
    assert findings["EPISODE_STEP_BUDGET_EXHAUSTED"]["impact"] == "TASK_BLOCKING"
    assert findings["UNCHANGED_READ_REPLAY"]["level"] == "VERIFIED"
    assert findings["REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET"]["layer"] == "mechanism"
    assert findings["REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET"]["missing_evidence"]
    assert report["root_cause_status"] == "OPEN"
    replay = next(item for item in report["evidence"] if item["evidence_id"] == "unchanged-read-replays")
    assert replay["data"]["redundant_call_count"] == 2
    assert replay["data"]["consumed_message_steps"] == 4
    assert report["transition_analysis"]["first_divergence"] is None


def test_completed_response_at_step_limit_is_unscored_not_business_blocked(tmp_path):
    final = "Done. The approved change was applied."
    result = _write(tmp_path / "task-20.json", {
        "task_id": "20", "official_reward": 0,
        "termination": "max_steps", "evaluation_scope": "termination_gate_only",
        "env": {"reward": 0, "db_check": None},
        "action": {"reward": 0, "action_checks": None},
        "target_trace": [{"turn": 4, "outcome": {"response": {
            "response_id": "publication-1", "response": final,
            "delivery_status": "selected", "task_completed": True,
            "evaluation_trace": {
                "outcome": {"kind": "COMPLETED", "task_completed": True},
                "state_side_effect": {"committed_receipt_refs": ["receipt-1"]},
            },
        }}}],
    })
    messages = [{"role": "assistant", "content": "Hello"}, {"role": "user", "content": "Start"}]
    for index in range(2):
        call_id = f"call-{index}"
        messages.extend([
            {"role": "assistant", "tool_calls": [{
                "id": call_id, "name": "get_order_details", "arguments": {"order_id": "W1"},
            }]},
            {"id": call_id, "role": "tool", "content": "same-order-state"},
        ])
    messages.extend([
        {"role": "assistant", "tool_calls": [{
            "id": "write-1", "name": "modify_pending_order_items", "arguments": {"order_id": "W1"},
        }]},
        {"id": "write-1", "role": "tool", "content": "updated"},
        {"role": "assistant", "content": final},
    ])
    trajectory = _write(tmp_path / "task-20-trajectory.json", {"messages": messages})

    report = analyze_task(result, trajectory, run_context={"max_steps": len(messages) - 1})

    findings = {item["code"]: item for item in report["findings"]}
    assert report["business_status"] == "COMPLETED_UNSCORED"
    assert findings["EPISODE_STEP_BUDGET_EXHAUSTED"]["impact"] == "EVALUATION_BLOCKING"
    replay = findings["REDUNDANT_READ_REPLAY_EXHAUSTED_TERMINATION_BUDGET"]
    assert replay["impact"] == "EVALUATION_BLOCKING"
    completion = next(
        item for item in report["evidence"]
        if item["evidence_id"] == "runtime-completion-at-step-limit"
    )
    assert completion["data"]["committed_receipt_refs"] == ["receipt-1"]


def test_missing_write_is_linked_to_task_bound_planning_failure(tmp_path):
    result = _write(tmp_path / "task-20.json", {
        "task_id": "20", "official_reward": 0,
        "env": {"reward": 0},
        "action": {"reward": 0, "action_checks": [
            _failed_write("modify_pending_order_items", {"order_id": "W1"})
        ]},
        "target_trace": [{
            "turn": 4,
            "input": "Go ahead and upgrade all items; I will pay the difference.",
            "outcome": {
                "response": {"evaluation_trace": {
                    "artifact": {"plan_id": "plan-4", "route_mode": "DIRECT"},
                    "consumption": {"work_items": [{
                        "work_item_id": "work-calculate", "control_mode": "DIRECT",
                        "allowed_tools": ["calculate"],
                    }]},
                    "state_side_effect": {"committed_receipt_refs": []},
                }},
                "stages": [{
                    "stage": "planning_observation", "status": "failed",
                    "detail": {
                        "code": "CONVERSATION_PROVIDER_OUTPUT_INVALID",
                        "exception_chain": [{"type": "PlanningUnavailable"}],
                    },
                }],
            },
        }],
    })

    report = analyze_task(result)

    findings = {item["code"]: item for item in report["findings"]}
    assert findings["PLANNING_STAGE_FAILURE"]["level"] == "VERIFIED"
    hypothesis = findings["ACTION_NOT_PREPARED_AFTER_PLANNING_FAILURE"]
    assert hypothesis["level"] == "SUPPORTED"
    assert hypothesis["layer"] == "causal_candidate"
    assert hypothesis["owner_candidate"] == "conversation_planning"
    assert findings["FAILED_TURN_REQUESTS_REQUIRED_ACTION"]["layer"] == "hypothesis"
    assert report["root_cause_status"] == "OPEN"
    path = report["transition_analysis"]["paths"][0]
    assert path["first_divergence"]["phase"] == "PLAN"
    assert [item["phase"] for item in path["transitions"]] == report["transition_analysis"]["phase_order"]


def test_recovered_planning_failure_is_not_reported_as_task_blocking(tmp_path):
    result = _write(tmp_path / "task-10.json", {
        "task_id": "10", "official_reward": 1,
        "env": {"reward": 1}, "action": {"reward": 1, "action_checks": []},
        "target_trace": [{
            "turn": 2,
            "outcome": {"stages": [{
                "stage": "planning", "status": "failed",
                "detail": {"code": "CONVERSATION_PROVIDER_OUTPUT_INVALID"},
            }]},
        }],
    })

    report = analyze_task(result)

    findings = {item["code"]: item for item in report["findings"]}
    assert findings["PLANNING_STAGE_FAILURE"]["impact"] == "RECOVERED"
    assert not report["transition_analysis"]["paths"]


def test_checkpoint_links_pending_action_to_failed_approval_turn(tmp_path):
    result = _write(tmp_path / "task-20.json", {
        "task_id": "20", "official_reward": 0,
        "env": {"reward": 0},
        "action": {"reward": 0, "action_checks": [
            _failed_write("modify_pending_order_items", {"order_id": "W1"})
        ]},
        "target_trace": [
            {"turn": 4, "outcome": {
                "kind": "APPROVAL", "signal_id": "approval-1",
            }},
            {
                "turn": 5,
                "input": "Yes, I approve those changes. Please go ahead.",
                "outcome": {"stages": [{
                    "stage": "planning", "status": "failed",
                    "detail": {"code": "CONTEXT_BUDGET_EXCEEDED"},
                }]},
            },
        ],
        "checkpoint_projection": {
            "status": "AVAILABLE",
            "snapshots": [{
                "sequence": 9, "thread_id": "approval-thread", "checkpoint_id": "cp-9",
                "lineage": [
                    {"path": "$.result.pending_action.allowed_tools", "field": "allowed_tools",
                     "value": ["modify_pending_order_items", "observed_operation_status"]},
                    {"path": "$.pending_approval.approval_id", "field": "approval_id",
                     "value": "approval-1"},
                    {"path": "$.result.pending_action.operation_key", "field": "operation_key",
                     "value": "operation-1"},
                ],
            }],
        },
    })

    report = analyze_task(result)

    findings = {item["code"]: item for item in report["findings"]}
    hypothesis = findings["ACTION_RESUME_BLOCKED"]
    assert hypothesis["level"] == "SUPPORTED"
    assert hypothesis["layer"] == "causal_candidate"
    assert hypothesis["owner_candidate"] == "context_admission"
    assert findings["APPROVAL_RESPONSE_MATCHES_PENDING_ACTION"]["layer"] == "hypothesis"
    checkpoint = next(
        item for item in report["evidence"]
        if item["evidence_id"] == "transition-0-lineage"
    )
    assert checkpoint["data"]["approval_ids"] == ["approval-1"]
    assert checkpoint["data"]["receipt_ids"] == []
    assert report["transition_analysis"]["first_divergence"]["phase"] == "APPROVAL"
    assert report["root_cause_status"] == "OPEN"


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


def test_successful_task_reports_quality_findings_without_changing_business_pass(tmp_path):
    _write(tmp_path / "manifest.json", {"status": "EVALUATED"})
    result = _write(tmp_path / "task-20.json", {
        "task_id": "20", "official_reward": 1,
        "env": {"reward": 1}, "action": {"reward": 1, "action_checks": []},
        "target_trace": [{
            "candidate": "unsupported draft",
            "verification": {
                "status": "reject", "reason_code": "ungrounded", "grounded": False,
                "assessment": {"issues": ["Unsupported item prices."]},
            },
        }],
    })
    trajectory = _write(tmp_path / "task-20-trajectory.json", {"messages": [
        {"role": "assistant", "tool_calls": [{
            "id": "call-1", "name": "get_product_details", "arguments": {"product_id": "p1"},
        }]},
        {"id": "call-1", "role": "tool", "content": "same product"},
        {"role": "assistant", "tool_calls": [{
            "id": "call-2", "name": "get_product_details", "arguments": {"product_id": "p1"},
        }]},
        {"id": "call-2", "role": "tool", "content": "same product"},
        {"role": "assistant", "content": (
            "Please confirm:\n- Item ids:\n  1. 123\n- Payment method id: gift_1\n"
            "Evidence [official-call:tau3-call-123]."
        )},
    ]})

    task = analyze_task(result, trajectory)
    report = analyze_run(tmp_path)

    codes = {item["code"] for item in task["findings"]}
    assert task["business_status"] == "PASS"
    assert task["quality_status"] == "WARN"
    assert {"PASS", "EXACT_READ_REPLAY", "PUBLIC_INTERNAL_REFERENCE_EXPOSURE",
            "PUBLIC_RAW_ACTION_SCHEMA_EXPOSURE", "RESPONSE_DRAFT_REJECTED"} <= codes
    assert report["quality_finding_counts"] == {
        "EXACT_READ_REPLAY": 1,
        "PUBLIC_INTERNAL_REFERENCE_EXPOSURE": 1,
        "PUBLIC_RAW_ACTION_SCHEMA_EXPOSURE": 1,
        "RESPONSE_DRAFT_REJECTED": 1,
    }
    assert {item["code"] for item in report["quality_clusters"]} == {
        "EXACT_READ_REPLAY",
        "PUBLIC_INTERNAL_REFERENCE_EXPOSURE",
        "PUBLIC_RAW_ACTION_SCHEMA_EXPOSURE",
        "RESPONSE_DRAFT_REJECTED",
    }


def test_read_refresh_after_write_is_not_an_exact_replay(tmp_path):
    result = _write(tmp_path / "task-1.json", {
        "task_id": "1", "official_reward": 1,
        "env": {"reward": 1}, "action": {"reward": 1, "action_checks": []},
    })
    trajectory = _write(tmp_path / "task-1-trajectory.json", {"messages": [
        {"role": "assistant", "tool_calls": [{
            "id": "read-1", "name": "get_order_details", "arguments": {"order_id": "W1"},
        }]},
        {"id": "read-1", "role": "tool", "content": "same order"},
        {"role": "assistant", "tool_calls": [{
            "id": "write-1", "name": "modify_order", "arguments": {"order_id": "W2"},
        }]},
        {"id": "write-1", "role": "tool", "content": "modified"},
        {"role": "assistant", "tool_calls": [{
            "id": "read-2", "name": "get_order_details", "arguments": {"order_id": "W1"},
        }]},
        {"id": "read-2", "role": "tool", "content": "same order"},
    ]})

    report = analyze_task(result, trajectory)

    assert report["business_status"] == "PASS"
    assert report["quality_status"] == "PASS"
    assert "EXACT_READ_REPLAY" not in {item["code"] for item in report["findings"]}


def test_run_clusters_first_divergences_by_phase_code_and_owner(tmp_path):
    _write(tmp_path / "manifest.json", {"status": "EVALUATED"})
    for task_id in ("1", "2"):
        _write(tmp_path / f"task-{task_id}.json", {
            "task_id": task_id, "official_reward": 0,
            "env": {"reward": 0},
            "action": {"reward": 0, "action_checks": [
                _failed_write("modify_order", {"id": task_id})
            ]},
        })

    report = analyze_run(tmp_path)

    assert report["failure_clusters"] == [{
        "phase": "ACTION_PREPARED",
        "code": "REQUIRED_ACTION_NOT_PREPARED",
        "owner_candidate": "task_planning",
        "case_count": 2,
        "task_ids": ["1", "2"],
    }]
