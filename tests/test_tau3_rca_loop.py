"""Causal probes, regression promotion, gates, and Langfuse publication."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from evaluation.tau3_causal_probes import probe_report
from evaluation.tau3_gate import compare_and_gate
from evaluation.tau3_langfuse_scores import publish_scores
from evaluation.tau3_langfuse_evidence import enrich_from_langfuse
from evaluation.tau3_llm_judge import evidence_packet, judge_report
from evaluation.tau3_regression import generate_candidates, promote_candidates


def _task(task_id="8", findings=None, env=0, action=0):
    return {
        "task_id": task_id,
        "result_file": f"task-{task_id}.json",
        "artifact_sha256": {"result": "a" * 64, "trajectory": "b" * 64},
        "langfuse_session_id": f"tau3-session-{task_id}",
        "outcome": {"env_reward": env, "action_reward": action},
        "root_cause_status": "OPEN",
        "findings": findings or [],
    }


def _report(tasks):
    return {"schema_version": "dialogpilot-tau3-rca-v2", "run": {"project_commit": "abc"}, "tasks": tasks}


def _finding(code, impact):
    return {
        "code": code, "impact": impact, "layer": "mechanism", "level": "VERIFIED",
        "evidence_ids": [f"evidence-{code}"],
    }


def test_goal_revision_probe_promotes_first_stale_consumer(tmp_path):
    task = _task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])
    task["evidence"] = [{
        "evidence_id": "evidence-MISSING_REQUIRED_WRITE",
        "data": {"expected": {"name": "exchange_items"}},
    }]
    (tmp_path / "task-8.json").write_text(json.dumps({"causal_events": [
        {"event_id": "e1", "sequence": 1, "event_type": "GOAL_REVISED", "task_id": "8",
         "turn_id": "t2", "owner": "conversation_state", "evidence_origin": "OWNER_EVENT",
         "control_id": "goal-1", "control_revision": 2},
        {"event_id": "e2", "sequence": 2, "event_type": "OUTCOME_REVIEWED", "task_id": "8",
         "turn_id": "t2", "owner": "domain_reviewer", "evidence_origin": "OWNER_EVENT",
         "control_id": "goal-1", "control_revision": 1, "action_name": "exchange_items"},
    ]}))

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["root_cause_status"] == "VERIFIED"
    root = enriched["tasks"][0]["findings"][-1]
    assert root["code"] == "STALE_GOAL_REVISION_CONSUMED"
    assert root["owner_candidate"] == "domain_reviewer"


def test_stale_revision_without_terminal_link_stays_inconclusive(tmp_path):
    task = _task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])
    (tmp_path / "task-8.json").write_text(json.dumps({"causal_events": [
        {"event_id": "e1", "sequence": 1, "event_type": "GOAL_REVISED", "task_id": "8",
         "turn_id": "t2", "owner": "conversation_state", "evidence_origin": "OWNER_EVENT",
         "control_id": "goal-1", "control_revision": 2},
        {"event_id": "e2", "sequence": 2, "event_type": "OUTCOME_REVIEWED", "task_id": "8",
         "turn_id": "t2", "owner": "domain_reviewer", "evidence_origin": "OWNER_EVENT",
         "control_id": "goal-1", "control_revision": 1},
    ]}))
    task["findings"] = []

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["root_cause_status"] == "OPEN"
    assert enriched["tasks"][0]["causal_probes"]["results"][0]["status"] == "INCONCLUSIVE"


def test_compaction_read_replay_probe_verifies_owner_root_and_structural_cluster(tmp_path):
    tasks = []
    for task_id in ("8", "20"):
        task = _task(task_id, findings=[
            _finding("EPISODE_STEP_BUDGET_EXHAUSTED", "TASK_BLOCKING"),
            _finding("REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET", "TASK_BLOCKING"),
        ])
        tasks.append(task)
        (tmp_path / f"task-{task_id}.json").write_text(json.dumps({"causal_events": [
            {"event_id": f"{task_id}-read", "sequence": 1, "event_type": "READ_OBSERVED",
             "task_id": task_id, "turn_id": "turn-1", "owner": "agent_progress",
             "evidence_origin": "OWNER_EVENT", "work_item_id": f"work-{task_id}",
             "read_identity": "same-read"},
            {"event_id": f"{task_id}-compact", "sequence": 2, "event_type": "CONTEXT_COMPACTED",
             "task_id": task_id, "turn_id": "turn-1", "owner": "working_context",
             "evidence_origin": "OWNER_EVENT", "work_item_id": f"work-{task_id}",
             "summary_applied": True},
            {"event_id": f"{task_id}-replay", "sequence": 3, "event_type": "READ_REPLAYED",
             "task_id": task_id, "turn_id": "turn-1", "owner": "agent_progress",
             "evidence_origin": "OWNER_EVENT", "work_item_id": f"work-{task_id}",
             "read_identity": "same-read", "guard_decision": "ALLOW_FIRST_REPLAY"},
        ]}))

    enriched = probe_report(_report(tasks), tmp_path)

    assert all(task["root_cause_status"] == "VERIFIED" for task in enriched["tasks"])
    root = enriched["tasks"][0]["findings"][-1]
    assert root["code"] == "READ_REUSE_CONTRACT_NOT_ENFORCED_AFTER_COMPACTION"
    assert root["causal_chain"]["first_bad_event_id"] == "8-replay"
    assert root["causal_chain"]["attributed_replay_event_count"] == 1
    assert root["causal_chain"]["scope"] == "SAME_WORK_ITEM_AFTER_COMPACTION"
    cluster, = enriched["failure_clusters"]
    assert cluster["cluster_kind"] == "VERIFIED_ROOT_CAUSE"
    assert cluster["case_count"] == 2
    assert cluster["task_ids"] == ["8", "20"]
    repeated = probe_report(enriched, tmp_path)
    assert sum(finding["code"] == root["code"]
               for finding in repeated["tasks"][0]["findings"]) == 1


def test_compaction_replay_root_preserves_evaluation_blocking_impact(tmp_path):
    task = _task("20", findings=[
        _finding("EPISODE_STEP_BUDGET_EXHAUSTED", "EVALUATION_BLOCKING"),
        _finding("REDUNDANT_READ_REPLAY_EXHAUSTED_TERMINATION_BUDGET", "EVALUATION_BLOCKING"),
    ])
    (tmp_path / "task-20.json").write_text(json.dumps({"causal_events": [
        {"event_id": "read", "sequence": 1, "event_type": "READ_OBSERVED", "task_id": "20",
         "turn_id": "t", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r"},
        {"event_id": "compact", "sequence": 2, "event_type": "CONTEXT_COMPACTED", "task_id": "20",
         "turn_id": "t", "owner": "working_context", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "summary_applied": True},
        {"event_id": "replay", "sequence": 3, "event_type": "READ_REPLAYED", "task_id": "20",
         "turn_id": "t", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r", "guard_decision": "ALLOW_FIRST_REPLAY"},
    ]}))

    enriched = probe_report(_report([task]), tmp_path)

    root = next(item for item in enriched["tasks"][0]["findings"] if item["layer"] == "root_cause")
    assert root["impact"] == "EVALUATION_BLOCKING"


def test_replay_scope_does_not_claim_event_counts_partition_tool_repeats(tmp_path):
    task = _task("20", findings=[_finding("EXACT_READ_REPLAY", "EFFICIENCY_DEGRADED")])
    task["evidence"] = [{
        "evidence_id": "exact-read-replays", "data": {"redundant_call_count": 19},
    }]
    (tmp_path / "task-20.json").write_text(json.dumps({"causal_events": [
        {"event_id": "first", "sequence": 1, "event_type": "READ_OBSERVED", "task_id": "20",
         "turn_id": "t1", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w1", "read_identity": "r"},
        {"event_id": "cross", "sequence": 2, "event_type": "READ_OBSERVED", "task_id": "20",
         "turn_id": "t2", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w2", "read_identity": "r", "guard_decision": "ALLOW_NEW_EVIDENCE"},
        {"event_id": "same", "sequence": 3, "event_type": "READ_REPLAYED", "task_id": "20",
         "turn_id": "t2", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w2", "read_identity": "r", "guard_decision": "WARN"},
        {"event_id": "blocked", "sequence": 4, "event_type": "READ_REPLAYED", "task_id": "20",
         "turn_id": "t2", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w2", "read_identity": "r", "guard_decision": "BLOCK"},
    ]}))

    enriched = probe_report(_report([task]), tmp_path)

    scope = next(
        probe for probe in enriched["tasks"][0]["causal_probes"]["results"]
        if probe["probe"] == "read_replay_scope"
    )
    assert scope["exact_redundant_tool_call_count"] == 19
    assert scope["event_scopes"] == {
        "cross_continuation_observed_count": 1,
        "same_work_item_allowed_replay_count": 1,
        "blocked_replay_attempt_count": 1,
    }
    assert scope["join_status"] == "UNJOINED"
    assert scope["missing_join_keys"] == [
        "source_business_call_id",
        "observation_read_call_id",
        "emitted_business_call_id",
        "reuse_decision",
        "business_call_binding",
    ]


def test_replay_scope_joins_exact_call_to_consumed_source_and_emission(tmp_path):
    task = _task("20", findings=[_finding("EXACT_READ_REPLAY", "EFFICIENCY_DEGRADED")])
    task["evidence"] = [{
        "evidence_id": "exact-read-replays", "data": {
            "redundant_call_count": 1,
            "groups": [{
                "tool": "get_order_details", "call_ids": ["outer-1", "outer-2"],
                "repeated_call_ids": ["outer-2"], "call_count": 2,
            }],
        },
    }]
    events = [
        {"event_id": "read", "sequence": 1, "event_type": "READ_OBSERVED", "task_id": "20",
         "turn_id": "t", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r", "observation_read_call_id": "internal-1",
         "source_business_call_ids": ["internal-1"]},
        {"event_id": "emit", "sequence": 2, "event_type": "READ_INFORMED_TOOL_EMISSION", "task_id": "20",
         "turn_id": "t", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r", "observation_read_call_id": "internal-1",
         "source_business_call_ids": ["internal-1"], "emitted_business_call_id": "internal-2",
         "action_name": "get_order_details"},
        {"event_id": "decision", "sequence": 3, "event_type": "READ_REUSE_DECIDED", "task_id": "20",
         "turn_id": "t", "owner": "tool_read_reuse", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r", "tool_call_id": "internal-2",
         "emitted_business_call_id": "internal-2", "reuse_decision": "POLICY_DISABLED"},
    ]
    target_trace = [{"causal_link": {
        "schema_version": "tau3-business-call-binding-v1", "internal_tool_call_id": internal,
        "business_call_id": outer, "tool_name": "get_order_details",
    }} for internal, outer in (("internal-1", "outer-1"), ("internal-2", "outer-2"))]
    (tmp_path / "task-20.json").write_text(json.dumps({
        "causal_events": events, "target_trace": target_trace,
    }))

    enriched = probe_report(_report([task]), tmp_path)
    scope = next(probe for probe in enriched["tasks"][0]["causal_probes"]["results"]
                 if probe["probe"] == "read_replay_scope")
    assert scope["join_status"] == "COMPLETE"
    assert scope["attributed_redundant_tool_call_count"] == 1
    call, = scope["per_call_attribution"]
    assert call["business_call_id"] == "outer-2" and call["status"] == "JOINED"
    chain, = call["causal_chains"]
    assert chain["source_business_call_ids"] == ["outer-1"]
    assert chain["cause_code"] == "READ_REUSE_POLICY_DISABLED"
    assert scope["attribution_clusters"][0]["call_count"] == 1
    assert enriched["read_replay_attribution_clusters"][0]["task_ids"] == ["20"]


def test_historical_observation_replay_is_attributed_to_missing_reusable_state(tmp_path):
    task = _task("21", findings=[_finding("EXACT_READ_REPLAY", "EFFICIENCY_DEGRADED")])
    task["evidence"] = [{"evidence_id": "exact-read-replays", "data": {
        "redundant_call_count": 1, "groups": [{
            "tool": "get_product_details", "call_ids": ["outer-1", "outer-2"], "call_count": 2,
        }],
    }}]
    events = [
        {"event_id": "historical-read", "sequence": 1, "event_type": "READ_OBSERVED", "task_id": "21",
         "turn_id": "t2", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w2", "read_identity": "r", "observation_read_call_id": "history-read",
         "source_business_call_ids": ["internal-1"]},
        {"event_id": "emission", "sequence": 2, "event_type": "READ_INFORMED_TOOL_EMISSION", "task_id": "21",
         "turn_id": "t2", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w2", "read_identity": "r", "observation_read_call_id": "history-read",
         "source_business_call_ids": ["internal-1"], "emitted_business_call_id": "internal-2"},
        {"event_id": "decision", "sequence": 3, "event_type": "READ_REUSE_DECIDED", "task_id": "21",
         "turn_id": "t2", "owner": "tool_read_reuse", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w2", "tool_call_id": "internal-2", "emitted_business_call_id": "internal-2",
         "reuse_decision": "NO_PRIOR_RESULT"},
    ]
    bindings = [{"causal_link": {"schema_version": "tau3-business-call-binding-v1",
        "internal_tool_call_id": internal, "business_call_id": outer,
        "tool_name": "get_product_details"}}
        for internal, outer in (("internal-1", "outer-1"), ("internal-2", "outer-2"))]
    (tmp_path / "task-21.json").write_text(json.dumps({
        "causal_events": events, "target_trace": bindings,
    }))

    enriched = probe_report(_report([task]), tmp_path)
    scope = next(probe for probe in enriched["tasks"][0]["causal_probes"]["results"]
                 if probe["probe"] == "read_replay_scope")
    chain, = scope["per_call_attribution"][0]["causal_chains"]
    assert scope["join_status"] == "COMPLETE"
    assert chain["source_path"] == "HISTORICAL_OBSERVATION"
    assert chain["cause_code"] == "REUSABLE_READ_STATE_MISSING"


def test_same_replay_symptom_with_different_trigger_does_not_join_root_cluster(tmp_path):
    compacted = _task("8", findings=[
        _finding("EPISODE_STEP_BUDGET_EXHAUSTED", "TASK_BLOCKING"),
        _finding("REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET", "TASK_BLOCKING"),
    ])
    unrelated = _task("9", findings=[
        _finding("REDUNDANT_READ_REPLAY_EXHAUSTED_STEP_BUDGET", "TASK_BLOCKING"),
    ])
    unrelated["transition_analysis"] = {"first_divergence": {
        "phase": "PLAN", "code": "REPEATED_READ_SELECTED", "owner_candidate": "task_planning",
    }}
    (tmp_path / "task-8.json").write_text(json.dumps({"causal_events": [
        {"event_id": "read", "sequence": 1, "event_type": "READ_OBSERVED", "task_id": "8",
         "turn_id": "t", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r"},
        {"event_id": "compact", "sequence": 2, "event_type": "CONTEXT_COMPACTED", "task_id": "8",
         "turn_id": "t", "owner": "working_context", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "summary_applied": True},
        {"event_id": "replay", "sequence": 3, "event_type": "READ_REPLAYED", "task_id": "8",
         "turn_id": "t", "owner": "agent_progress", "evidence_origin": "OWNER_EVENT",
         "work_item_id": "w", "read_identity": "r", "guard_decision": "ALLOW_FIRST_REPLAY"},
    ]}))
    (tmp_path / "task-9.json").write_text("{}")

    enriched = probe_report(_report([compacted, unrelated]), tmp_path)

    assert len(enriched["failure_clusters"]) == 2
    assert enriched["failure_clusters"][0]["task_ids"] == ["8"]
    assert enriched["failure_clusters"][1]["task_ids"] == ["9"]


def test_cross_continuation_known_read_is_verified_as_quality_root(tmp_path):
    task = _task("20", findings=[{
        "code": "EXACT_READ_REPLAY", "layer": "quality", "level": "OBSERVED",
        "summary": "same read and result", "owner_candidate": "agent_read_planning",
        "evidence_ids": ["exact-read-replays"], "missing_evidence": [],
        "impact": "EFFICIENCY_DEGRADED",
    }], env=1, action=1)
    task["quality_status"] = "WARN"
    (tmp_path / "task-20.json").write_text(json.dumps({}))
    task["causal_events"] = [
        {"event_id": "read-1", "sequence": 1, "event_type": "READ_OBSERVED",
         "task_id": "20", "turn_id": "turn-1", "owner": "agent_progress",
         "evidence_origin": "OWNER_EVENT", "work_item_id": "work-1",
         "read_identity": "read-a", "guard_decision": "ALLOW_NEW_EVIDENCE"},
        {"event_id": "compact-1", "sequence": 2, "event_type": "CONTEXT_COMPACTED",
         "task_id": "20", "turn_id": "turn-2", "owner": "working_context",
         "evidence_origin": "OWNER_EVENT", "work_item_id": "work-2",
         "summary_applied": True},
        {"event_id": "read-2", "sequence": 3, "event_type": "READ_OBSERVED",
         "task_id": "20", "turn_id": "turn-2", "owner": "agent_progress",
         "evidence_origin": "OWNER_EVENT", "work_item_id": "work-2",
         "read_identity": "read-a", "guard_decision": "ALLOW_NEW_EVIDENCE"},
    ]

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["root_cause_status"] == "OPEN"
    assert enriched["tasks"][0]["quality_root_cause_status"] == "VERIFIED"
    root = next(item for item in enriched["tasks"][0]["findings"]
                if item["layer"] == "quality_root_cause")
    assert root["code"] == "READ_NOVELTY_AUTHORITY_SCOPED_TOO_NARROWLY"
    assert root["causal_chain"]["first_bad_event_id"] == "read-2"
    assert root["causal_chain"]["replayed_read_count"] == 1
    assert enriched["quality_root_clusters"][0]["task_ids"] == ["20"]


def test_authorized_cross_continuation_refresh_is_not_a_quality_root(tmp_path):
    task = _task("20", findings=[{
        "code": "EXACT_READ_REPLAY", "layer": "quality", "level": "OBSERVED",
        "evidence_ids": ["exact-read-replays"], "impact": "EFFICIENCY_DEGRADED",
    }], env=1, action=1)
    (tmp_path / "task-20.json").write_text(json.dumps({}))
    task["causal_events"] = [
        {"event_id": "read-1", "sequence": 1, "event_type": "READ_OBSERVED",
         "task_id": "20", "turn_id": "turn-1", "owner": "agent_progress",
         "evidence_origin": "OWNER_EVENT", "work_item_id": "work-1",
         "read_identity": "read-a", "guard_decision": "ALLOW_NEW_EVIDENCE"},
        {"event_id": "compact-1", "sequence": 2, "event_type": "CONTEXT_COMPACTED",
         "task_id": "20", "turn_id": "turn-2", "owner": "working_context",
         "evidence_origin": "OWNER_EVENT", "work_item_id": "work-2",
         "summary_applied": True},
        {"event_id": "read-2", "sequence": 3, "event_type": "READ_OBSERVED",
         "task_id": "20", "turn_id": "turn-2", "owner": "agent_progress",
         "evidence_origin": "OWNER_EVENT", "work_item_id": "work-2",
         "read_identity": "read-a", "guard_decision": "ALLOW_AUTHORIZED_REFRESH"},
    ]

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["quality_root_cause_status"] == "OPEN"
    assert enriched["quality_root_clusters"] == []


def test_stale_revision_with_unrelated_failure_stays_inconclusive(tmp_path):
    task = _task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])
    task["evidence"] = [{
        "evidence_id": "evidence-MISSING_REQUIRED_WRITE",
        "data": {"expected": {"name": "return_items"}},
    }]
    (tmp_path / "task-8.json").write_text(json.dumps({"causal_events": [
        {"event_id": "e1", "sequence": 1, "event_type": "GOAL_REVISED", "task_id": "8",
         "turn_id": "t2", "owner": "conversation_state", "evidence_origin": "OWNER_EVENT",
         "control_id": "goal-1", "control_revision": 2},
        {"event_id": "e2", "sequence": 2, "event_type": "OUTCOME_REVIEWED", "task_id": "8",
         "turn_id": "t2", "owner": "domain_reviewer", "evidence_origin": "OWNER_EVENT",
         "control_id": "goal-1", "control_revision": 1, "action_name": "exchange_items"},
    ]}))

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["root_cause_status"] == "OPEN"
    assert enriched["tasks"][0]["causal_probes"]["results"][0]["status"] == "INCONCLUSIVE"


def test_probe_stays_inconclusive_without_causal_lineage(tmp_path):
    task = _task()
    (tmp_path / "task-8.json").write_text("{}")

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["root_cause_status"] == "OPEN"
    assert enriched["tasks"][0]["causal_probes"]["results"][0]["status"] == "INCONCLUSIVE"


def test_probe_rejects_non_owner_causal_claims(tmp_path):
    task = _task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])
    (tmp_path / "task-8.json").write_text(json.dumps({"causal_events": [
        {"event_id": "judge-e1", "sequence": 1, "event_type": "GOAL_REVISED", "task_id": "8",
         "turn_id": "t2", "owner": "llm_judge", "evidence_origin": "LLM_INFERENCE",
         "control_id": "goal-1", "control_revision": 2},
    ]}))

    enriched = probe_report(_report([task]), tmp_path)

    assert enriched["tasks"][0]["root_cause_status"] == "OPEN"
    assert enriched["tasks"][0]["causal_probes"]["contract_status"] == "INVALID"


def test_probe_accepts_checkpoint_backed_transition_projection(tmp_path):
    task = _task()
    (tmp_path / "task-8.json").write_text(json.dumps({"checkpoint_events": [
        {"event_id": "cp-e1", "sequence": 1, "event_type": "GOAL_REVISED", "task_id": "8",
         "turn_id": "t2", "owner": "conversation_state",
         "evidence_origin": "CHECKPOINT_PROJECTION", "control_id": "goal-1",
         "control_revision": 2},
    ]}))

    enriched = probe_report(_report([task]), tmp_path)

    probes = enriched["tasks"][0]["causal_probes"]
    assert probes["contract_status"] == "VALID"
    assert probes["results"][0]["status"] == "PASS"


def test_regression_requires_review_and_gate_blocks_recurrence():
    failed = _report([_task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])])
    candidates = generate_candidates(failed)
    candidate_id = candidates["candidates"][0]["candidate_id"]
    assert candidates["candidates"][0]["diagnosis"]["root_cause_status"] == "OPEN"
    with pytest.raises(ValueError, match="missing review fields"):
        promote_candidates(candidates, {"reviews": [{"candidate_id": candidate_id, "decision": "APPROVE"}]})
    suite = promote_candidates(candidates, {"reviews": [{
        "candidate_id": candidate_id, "decision": "APPROVE", "reviewer": "qa@example.test",
        "reviewed_at": "2026-09-09T10:00:00+00:00",
    }]})

    still_failed = compare_and_gate(failed, failed, suite)
    repaired = _report([_task(findings=[], env=1, action=1)])
    now_passes = compare_and_gate(failed, repaired, suite)

    assert still_failed["status"] == "FAIL" and still_failed["exit_code"] == 1
    assert now_passes["status"] == "PASS" and now_passes["exit_code"] == 0


def test_gate_separates_missing_task_from_business_regression():
    baseline = _report([_task("8", env=1, action=1)])
    candidate = _report([])

    result = compare_and_gate(baseline, candidate)

    assert result["status"] == "INCOMPLETE"
    assert result["failures"] == []
    assert result["unavailable"][0]["task_id"] == "8"


def test_action_reference_drop_requires_explicit_gate_policy():
    baseline = _report([_task("4", env=1, action=1)])
    candidate = _report([_task("4", env=1, action=0)])

    default = compare_and_gate(baseline, candidate)
    strict = compare_and_gate(
        baseline, candidate, policy={
            "comparable_scores": ["env_reward", "action_reward"],
            "fail_on_missing_tasks": True,
        },
    )

    assert default["status"] == "PASS"
    assert strict["status"] == "FAIL"


def test_langfuse_scores_are_session_bound_and_stable():
    calls = []
    class Client:
        def create_score(self, **kwargs):
            calls.append(kwargs)
        def flush(self):
            calls.append({"flush": True})
    report = _report([_task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])])

    first = publish_scores(report, Client())
    first_ids = [item["score_id"] for item in calls if "score_id" in item]
    calls.clear()
    publish_scores(deepcopy(report), Client())
    second_ids = [item["score_id"] for item in calls if "score_id" in item]

    assert len(first["published"]) == 4
    assert first_ids == second_ids
    assert all(item["session_id"] == "tau3-session-8" for item in calls if "session_id" in item)


def test_langfuse_does_not_turn_missing_env_score_into_business_zero():
    calls = []
    class Client:
        def create_score(self, **kwargs): calls.append(kwargs)
    task = _task()
    task["outcome"]["env_reward"] = None
    task["findings"] = [_finding("PROVIDER_DEPENDENCY_FAILURE", "RUN_BLOCKING")]

    result = publish_scores(_report([task]), Client())

    assert "tau3_business_pass" not in {item["name"] for item in calls}
    assert next(item for item in calls if item["name"] == "tau3_run_available")["value"] == 0.0
    assert {item["reason"] for item in result["skipped"]} == {"business score unavailable"}


def test_missing_candidate_business_score_makes_gate_incomplete():
    baseline = _report([_task("8", env=1, action=1)])
    candidate_task = _task("8", env=1, action=1)
    candidate_task["outcome"]["env_reward"] = None

    result = compare_and_gate(baseline, _report([candidate_task]))

    assert result["status"] == "INCOMPLETE"
    assert result["failures"] == []


def test_langfuse_enrichment_extracts_only_standardized_causal_metadata():
    observation_requests = []
    class TraceAPI:
        def list(self, **kwargs):
            return {"data": [{"id": "trace-1"}]}
    class ObservationAPI:
        def get_many(self, **kwargs):
            observation_requests.append(kwargs)
            return {"data": [
                {
                    "id": "obs-0", "traceId": "trace-1", "type": "GENERATION",
                    "name": "worker-model", "startTime": "2026-09-09T10:00:00Z",
                    "endTime": "2026-09-09T10:00:01Z",
                    "usageDetails": {
                        "input": 100, "output": 20, "cache_read_input_tokens": 30,
                        "total": 150,
                    },
                    "costDetails": {"input": 0.01, "output": 0.02, "total": 0.03},
                    "metadata": {"dialogpilot.work_item_id": "work-1"},
                },
                {
                    "id": "obs-1", "traceId": "trace-1", "parentObservationId": "obs-0",
                    "type": "SPAN", "name": "review", "level": "ERROR",
                    "status_message": "stale revision",
                    "metadata": {
                        "causal.event_id": "event-1", "causal.sequence": "2",
                        "causal.event_type": "OUTCOME_REVIEWED", "causal.task_id": "8",
                        "causal.turn_id": "turn-2", "causal.owner": "domain_reviewer",
                        "causal.evidence_origin": "OWNER_EVENT", "causal.control_id": "goal-1",
                        "causal.control_revision": "2", "private": "not exported",
                        "causal.read_identity": "read-hash", "causal.guard_decision": "ALLOW_FIRST_REPLAY",
                        "causal.stagnant_rounds": "1", "causal.summary_applied": "true",
                        "causal.observation_read_call_id": "read-1",
                        "causal.emitted_business_call_id": "business-2",
                        "causal.source_business_call_ids": '["business-1"]',
                        "causal.reuse_decision": "POLICY_DISABLED",
                        "causal.refresh_requested": "false",
                    },
                },
                {
                    "id": "obs-2", "traceId": "trace-1", "type": "SPAN",
                    "name": "await_resume", "level": "DEFAULT",
                    "status_message": "expected approval interrupt", "metadata": {},
                },
            ], "meta": {"cursor": None}}
    class Client:
        api = type("API", (), {"trace": TraceAPI(), "observations": ObservationAPI()})()

    enriched = enrich_from_langfuse(_report([_task()]), Client())

    task = enriched["tasks"][0]
    assert task["causal_events"][0]["control_revision"] == 2
    assert task["causal_events"][0]["read_identity"] == "read-hash"
    assert task["causal_events"][0]["stagnant_rounds"] == 1
    assert task["causal_events"][0]["summary_applied"] is True
    assert task["causal_events"][0]["source_business_call_ids"] == ["business-1"]
    assert task["causal_events"][0]["observation_read_call_id"] == "read-1"
    assert task["causal_events"][0]["emitted_business_call_id"] == "business-2"
    assert task["causal_events"][0]["refresh_requested"] is False
    assert "private" not in task["langfuse_evidence"]["errors"][0]["join_keys"]
    assert [item["name"] for item in task["langfuse_evidence"]["status_events"]] == ["await_resume"]
    assert task["langfuse_evidence"]["token_usage"] == {
        "status": "COMPLETE", "generation_count": 1,
        "measured_generation_count": 1, "missing_generation_count": 0,
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 150,
        "additional_usage": {"cache_read_input_tokens": 30},
    }
    chain = task["langfuse_evidence"]["execution_chain"]
    assert [item["observation_id"] for item in chain] == ["obs-0", "obs-1", "obs-2"]
    assert chain[1]["parent_observation_id"] == "obs-0"
    assert chain[0]["usage"]["cache_read_input_tokens"] == 30
    assert chain[0]["cost"]["total_cost"] == pytest.approx(0.03)
    assert "usage" in observation_requests[0]["fields"]
    assert "metadata" in observation_requests[0]["fields"]


def test_llm_judge_supports_hypothesis_but_cannot_verify_root_cause():
    import asyncio
    task = _task(findings=[{
        "code": "GOAL_REVISION_NOT_APPLIED", "layer": "hypothesis", "level": "SUPPORTED",
        "summary": "old goal may remain", "owner_candidate": "goal_state_transition",
        "evidence_ids": ["revision"], "missing_evidence": [], "impact": "CAUSAL_CANDIDATE",
    }])
    task["evidence"] = [{"evidence_id": "revision", "summary": "user changed scope", "data": {}}]
    async def judge(packet):
        return {"judgments": [{
            "finding_code": "GOAL_REVISION_NOT_APPLIED", "verdict": "SUPPORTS",
            "first_bad_event_id": None, "owner_candidate": "goal_state_transition",
            "evidence_ids": ["revision"], "explanation": "The supplied revision supports the hypothesis.",
            "missing_evidence": ["linked reviewer input"],
        }]}

    result = asyncio.run(judge_report(_report([task]), judge))

    assert result["tasks"][0]["llm_judge"]["judgments"][0]["verdict"] == "SUPPORTS"
    assert result["tasks"][0]["root_cause_status"] == "OPEN"


def test_llm_judge_packet_excludes_runtime_causal_candidates():
    task = _task(findings=[
        {
            "code": "ACTION_RESUME_BLOCKED", "layer": "causal_candidate",
            "level": "SUPPORTED", "summary": "runtime boundary",
            "owner_candidate": "context_admission", "evidence_ids": ["lineage"],
            "missing_evidence": [], "impact": "CAUSAL_CANDIDATE",
        },
        {
            "code": "APPROVAL_RESPONSE_MATCHES_PENDING_ACTION", "layer": "hypothesis",
            "level": "SUPPORTED", "summary": "semantic edge", "owner_candidate": None,
            "evidence_ids": ["lineage"], "missing_evidence": [],
            "impact": "CAUSAL_CANDIDATE",
        },
    ])
    task["evidence"] = [{"evidence_id": "lineage", "summary": "bounded", "data": {}}]

    packet = evidence_packet(task)

    assert [item["finding_code"] for item in packet["hypotheses"]] == [
        "APPROVAL_RESPONSE_MATCHES_PENDING_ACTION",
    ]


def test_llm_judge_isolates_hallucinated_evidence_reference():
    import asyncio
    task = _task(findings=[{
        "code": "GOAL_REVISION_NOT_APPLIED", "layer": "hypothesis", "level": "SUPPORTED",
        "summary": "old goal may remain", "owner_candidate": None,
        "evidence_ids": [], "missing_evidence": [], "impact": "CAUSAL_CANDIDATE",
    }])
    task["evidence"] = []
    async def judge(packet):
        return {"judgments": [{
            "finding_code": "GOAL_REVISION_NOT_APPLIED", "verdict": "SUPPORTS",
            "first_bad_event_id": "invented", "owner_candidate": "reviewer",
            "evidence_ids": ["invented"], "explanation": "unsupported", "missing_evidence": [],
        }]}

    result = asyncio.run(judge_report(_report([task]), judge))

    assert result["tasks"][0]["llm_judge"]["status"] == "INVALID"
    assert result["tasks"][0]["root_cause_status"] == "OPEN"


def test_llm_judge_outage_does_not_abort_other_evaluators():
    import asyncio
    task = _task(findings=[{
        "code": "GOAL_REVISION_NOT_APPLIED", "layer": "hypothesis", "level": "SUPPORTED",
        "summary": "old goal may remain", "owner_candidate": None,
        "evidence_ids": [], "missing_evidence": [], "impact": "CAUSAL_CANDIDATE",
    }])
    async def judge(_packet):
        raise TimeoutError("provider detail must not leak")

    result = asyncio.run(judge_report(_report([task]), judge))

    judged = result["tasks"][0]["llm_judge"]
    assert judged["status"] == "UNAVAILABLE"
    assert judged["error_type"] == "TimeoutError"
    assert result["tasks"][0]["root_cause_status"] == "OPEN"
