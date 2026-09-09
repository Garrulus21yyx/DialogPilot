"""Required-transition RCA finds the first unsupported state transition."""
from __future__ import annotations

from evaluation.tau3_transition_rca import PHASES, analyze_required_transitions


def _failed_write(name="modify_order"):
    return {"action": {"name": name, "arguments": {"id": "1"}}, "tool_type": "write"}


def _analyze(result, *, actual=(), failures=()):
    return analyze_required_transitions(
        result, required_writes=[_failed_write()], actual_writes=list(actual),
        planning_failures=list(failures),
    )


def test_unprepared_write_diverges_at_action_preparation():
    report = _analyze({"env": {"reward": 0}, "target_trace": []})

    assert report["phase_order"] == list(PHASES)
    assert report["first_divergence"]["phase"] == "ACTION_PREPARED"
    assert report["first_divergence"]["code"] == "REQUIRED_ACTION_NOT_PREPARED"


def test_prepared_write_without_terminal_transition_diverges_at_execution():
    report = _analyze({
        "env": {"reward": 0},
        "checkpoint_projection": {"status": "AVAILABLE", "snapshots": [{
            "sequence": 1, "thread_id": "thread", "lineage": [{
                "path": "$.result.pending_action.allowed_tools", "field": "allowed_tools",
                "value": ["modify_order"],
            }],
        }]},
    })

    assert report["first_divergence"]["phase"] == "EXECUTION"
    assert report["first_divergence"]["code"] == "PREPARED_ACTION_NOT_COMMITTED"


def test_approval_response_and_context_failure_diverge_at_approval_resume():
    report = _analyze({
        "env": {"reward": 0},
        "target_trace": [
            {"turn": 4, "outcome": {"kind": "APPROVAL", "signal_id": "approval-1"}},
            {"turn": 5, "input": "Yes, proceed."},
        ],
        "checkpoint_projection": {"status": "AVAILABLE", "snapshots": [{
            "sequence": 1, "thread_id": "thread", "lineage": [
                {"path": "$.result.pending_action.allowed_tools", "field": "allowed_tools",
                 "value": ["modify_order"]},
                {"path": "$.pending_approval.approval_id", "field": "approval_id",
                 "value": "approval-1"},
            ],
        }]},
    }, failures=[{"turn": 5, "stage": "planning", "code": "CONTEXT_BUDGET_EXCEEDED"}])

    divergence = report["first_divergence"]
    assert divergence["phase"] == "APPROVAL"
    assert divergence["code"] == "ACTION_RESUME_BLOCKED"
    assert divergence["owner_candidate"] == "context_admission"
    semantic = next(item for item in report["hypotheses"] if item["layer"] == "hypothesis")
    assert semantic["code"] == "APPROVAL_RESPONSE_MATCHES_PENDING_ACTION"


def test_context_admission_trace_identifies_pre_binding_approval_failure():
    report = analyze_required_transitions(
        {
            "official_reward": 0.0,
            "env": {"reward": 0.0},
            "checkpoint_projection": {"status": "AVAILABLE", "snapshots": [{
                "sequence": 1, "thread_id": "thread", "lineage": [
                    {"path": "$.result.pending_action.allowed_tools", "field": "allowed_tools",
                     "value": ["modify_order"]},
                    {"path": "$.pending_approval.approval_id", "field": "approval_id",
                     "value": "approval-1"},
                ],
            }]},
            "target_trace": [
                {"turn": 4, "outcome": {"kind": "APPROVAL", "signal_id": "approval-1"}},
                {"turn": 5, "input": "Yes, approve it"},
            ],
        },
        required_writes=[_failed_write()],
        actual_writes=[],
        planning_failures=[{
            "turn": 5,
            "stage": "planning",
            "code": "CONTEXT_BUDGET_EXCEEDED",
            "detail": {
                "boundary": "provider_request",
                "approval_binding_status": "SEMANTIC_RESOLUTION_NOT_REACHED",
            },
        }],
    )

    divergence = report["first_divergence"]
    assert divergence["code"] == "APPROVAL_RESOLUTION_BLOCKED_BY_CONTEXT_ADMISSION"
    assert divergence["owner_candidate"] == "context_admission"
    assert "deterministic approval binding" not in " ".join(divergence["missing_evidence"])
    semantic = next(item for item in report["hypotheses"] if item["layer"] == "hypothesis")
    assert semantic["code"] == "APPROVAL_RESPONSE_MATCHES_PENDING_ACTION"


def test_approval_semantic_evidence_includes_the_preceding_assistant_scope():
    report = analyze_required_transitions(
        {
            "env": {"reward": 0},
            "target_trace": [
                {"turn": 4, "outcome": {"kind": "APPROVAL", "signal_id": "approval-1"}},
                {"turn": 5, "input": "Yes, approve those changes."},
            ],
            "checkpoint_projection": {"status": "AVAILABLE", "snapshots": [{
                "sequence": 1, "thread_id": "thread", "lineage": [{
                    "path": "$.result.pending_action.allowed_tools", "field": "allowed_tools",
                    "value": ["modify_order"],
                }, {
                    "path": "$.pending_approval.approval_id", "field": "approval_id",
                    "value": "approval-1",
                }],
            }]},
        },
        required_writes=[_failed_write()], actual_writes=[],
        planning_failures=[{"turn": 5, "stage": "planning", "code": "failure"}],
        conversation_messages=[
            {"role": "assistant", "content": "Confirm modify order 1 to item B."},
            {"role": "user", "content": "Yes, approve those changes."},
        ],
    )

    lineage = report["evidence"][0]["data"]
    assert lineage["conversation_window"] == [
        {"role": "assistant", "content": "Confirm modify order 1 to item B."},
        {"role": "user", "content": "Yes, approve those changes."},
    ]


def test_unrelated_approval_signal_is_not_joined_to_pending_action():
    report = analyze_required_transitions(
        {
            "env": {"reward": 0},
            "target_trace": [
                {"turn": 4, "outcome": {"kind": "APPROVAL", "signal_id": "other"}},
                {"turn": 5, "input": "Yes, proceed."},
            ],
            "checkpoint_projection": {"status": "AVAILABLE", "snapshots": [{
                "sequence": 1, "thread_id": "thread", "lineage": [
                    {"path": "$.result.pending_action.allowed_tools", "field": "allowed_tools",
                     "value": ["modify_order"]},
                    {"path": "$.pending_approval.approval_id", "field": "approval_id",
                     "value": "approval-1"},
                ],
            }]},
        },
        required_writes=[_failed_write()], actual_writes=[],
        planning_failures=[{"turn": 5, "stage": "planning", "code": "failure"}],
    )

    assert report["first_divergence"]["code"] == "PREPARED_ACTION_NOT_COMMITTED"
    assert not any(
        item["code"] == "APPROVAL_RESPONSE_MATCHES_PENDING_ACTION"
        for item in report["hypotheses"]
    )


def test_wrong_write_arguments_diverge_at_execution_without_requiring_fixed_sequence():
    report = _analyze(
        {"env": {"reward": 0}},
        actual=[{"name": "modify_order", "arguments": {"id": "2"}}],
    )

    divergence = report["first_divergence"]
    assert divergence["phase"] == "EXECUTION"
    assert divergence["code"] == "WRITE_ARGUMENT_SELECTION_DIVERGED"


def test_each_required_write_gets_an_independent_path():
    report = analyze_required_transitions(
        {"env": {"reward": 0}},
        required_writes=[_failed_write("modify_order"), _failed_write("cancel_order")],
        actual_writes=[], planning_failures=[],
    )

    assert [path["required_action"] for path in report["paths"]] == [
        "modify_order", "cancel_order",
    ]


def test_matched_write_with_wrong_final_state_diverges_at_state():
    expected = _failed_write()
    expected["action_match"] = True
    report = analyze_required_transitions(
        {"env": {"reward": 0}, "official_reward": 0},
        required_writes=[expected],
        actual_writes=[{"name": "modify_order", "arguments": {"id": "1"}}],
        planning_failures=[],
    )

    assert report["first_divergence"]["phase"] == "STATE"
    assert report["first_divergence"]["code"] == "STATE_OUTCOME_MISMATCH"


def test_state_pass_with_overall_failure_diverges_at_answer():
    report = analyze_required_transitions(
        {"env": {"reward": 1}, "action": {"reward": 1}, "official_reward": 0},
        required_writes=[], actual_writes=[], planning_failures=[],
    )

    assert report["first_divergence"]["phase"] == "ANSWER"
    assert report["first_divergence"]["code"] == "ANSWER_OR_COMMUNICATION_MISMATCH"


def test_termination_gate_reward_is_not_relabelled_as_state_failure():
    report = analyze_required_transitions(
        {
            "termination": "max_steps",
            "evaluation_scope": "termination_gate_only",
            "env": {"reward": 0, "db_check": None},
            "action": {"reward": 0, "action_checks": None},
            "official_reward": 0,
        },
        required_writes=[], actual_writes=[], planning_failures=[],
    )

    assert report["paths"] == []
    assert report["first_divergence"] is None
