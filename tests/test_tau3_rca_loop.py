"""Causal probes, regression promotion, gates, and Langfuse publication."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from evaluation.tau3_causal_probes import probe_report
from evaluation.tau3_gate import compare_and_gate
from evaluation.tau3_langfuse_scores import publish_scores
from evaluation.tau3_langfuse_evidence import enrich_from_langfuse
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
    return {"code": code, "impact": impact, "layer": "mechanism", "level": "VERIFIED"}


def test_goal_revision_probe_promotes_first_stale_consumer(tmp_path):
    task = _task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])
    (tmp_path / "task-8.json").write_text(json.dumps({"causal_events": [
        {"event_id": "e1", "sequence": 1, "event_type": "GOAL_REVISED", "task_id": "8",
         "turn_id": "t2", "owner": "conversation_state", "goal_revision_id": "rev2"},
        {"event_id": "e2", "sequence": 2, "event_type": "OUTCOME_REVIEWED", "task_id": "8",
         "turn_id": "t2", "owner": "domain_reviewer", "goal_revision_id": "rev1",
         "outcome_impact": "TASK_BLOCKING"},
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
         "turn_id": "t2", "owner": "conversation_state", "goal_revision_id": "rev2"},
        {"event_id": "e2", "sequence": 2, "event_type": "OUTCOME_REVIEWED", "task_id": "8",
         "turn_id": "t2", "owner": "domain_reviewer", "goal_revision_id": "rev1"},
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


def test_regression_requires_review_and_gate_blocks_recurrence():
    failed = _report([_task(findings=[_finding("MISSING_REQUIRED_WRITE", "TASK_BLOCKING")])])
    candidates = generate_candidates(failed)
    candidate_id = candidates["candidates"][0]["candidate_id"]
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
    class TraceAPI:
        def list(self, **kwargs):
            return {"data": [{"id": "trace-1"}]}
    class ObservationAPI:
        def get_many(self, **kwargs):
            return {"data": [{
                "id": "obs-1", "traceId": "trace-1", "name": "review", "level": "ERROR",
                "status_message": "stale revision",
                "metadata": {
                    "causal.event_id": "event-1", "causal.sequence": "2",
                    "causal.event_type": "OUTCOME_REVIEWED", "causal.task_id": "8",
                    "causal.turn_id": "turn-2", "causal.owner": "domain_reviewer",
                    "causal.goal_revision_id": "rev-1", "private": "not exported",
                },
            }], "meta": {"cursor": None}}
    class Client:
        api = type("API", (), {"trace": TraceAPI(), "observations": ObservationAPI()})()

    enriched = enrich_from_langfuse(_report([_task()]), Client())

    task = enriched["tasks"][0]
    assert task["causal_events"][0]["goal_revision_id"] == "rev-1"
    assert "private" not in task["langfuse_evidence"]["errors"][0]["join_keys"]
