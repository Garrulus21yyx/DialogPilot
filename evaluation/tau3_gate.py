"""Baseline comparison and CI gate for tau3 RCA reports."""
from __future__ import annotations

from typing import Any, Mapping

from evaluation.tau3_regression import evaluate_regressions


_BLOCKING_IMPACTS = {"TASK_BLOCKING", "OUTCOME_DEVIATION", "RUN_BLOCKING"}


def compare_and_gate(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    regression_suite: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    policy = policy or {"comparable_scores": ["env_reward"], "fail_on_missing_tasks": True}
    baseline_tasks = {str(task["task_id"]): task for task in baseline.get("tasks", [])}
    candidate_tasks = {str(task["task_id"]): task for task in candidate.get("tasks", [])}
    comparisons = []
    failures = []
    unavailable = []

    for task_id in sorted(set(baseline_tasks) | set(candidate_tasks), key=_task_sort_key):
        before = baseline_tasks.get(task_id)
        after = candidate_tasks.get(task_id)
        if before is None or after is None:
            unavailable.append({"task_id": task_id, "reason": "task absent from baseline or candidate"})
            continue
        before_codes = _blocking_codes(before)
        after_codes = _blocking_codes(after)
        new_codes = sorted(after_codes - before_codes)
        reward_drops = _reward_drops(
            before.get("outcome", {}), after.get("outcome", {}),
            policy.get("comparable_scores", ["env_reward"]),
        )
        missing_scores = [
            name for name in policy.get("comparable_scores", ["env_reward"])
            if before.get("outcome", {}).get(name) is not None
            and after.get("outcome", {}).get(name) is None
        ]
        if missing_scores:
            unavailable.append({
                "task_id": task_id,
                "reason": "candidate scores unavailable: " + ",".join(missing_scores),
            })
        comparison = {
            "task_id": task_id,
            "new_blocking_findings": new_codes,
            "resolved_blocking_findings": sorted(before_codes - after_codes),
            "reward_drops": reward_drops,
        }
        comparisons.append(comparison)
        if new_codes or reward_drops:
            failures.append(comparison)

    regression_results = evaluate_regressions(candidate, regression_suite or {"regressions": []})
    regression_failures = [item for item in regression_results if item["status"] != "PASS"]
    if unavailable and policy.get("fail_on_missing_tasks", True):
        gate_status = "INCOMPLETE"
        exit_code = 2
    elif failures or regression_failures:
        gate_status = "FAIL"
        exit_code = 1
    else:
        gate_status = "PASS"
        exit_code = 0
    return {
        "schema_version": "dialogpilot-tau3-gate-v1",
        "status": gate_status,
        "exit_code": exit_code,
        "comparisons": comparisons,
        "regression_results": regression_results,
        "failures": failures,
        "regression_failures": regression_failures,
        "unavailable": unavailable,
        "policy": policy,
    }


def _blocking_codes(task: Mapping[str, Any]) -> set[str]:
    return {
        str(item["code"]) for item in task.get("findings", [])
        if item.get("impact") in _BLOCKING_IMPACTS
    }


def _reward_drops(
    before: Mapping[str, Any], after: Mapping[str, Any], score_names
) -> list[Mapping[str, Any]]:
    drops = []
    for name in score_names:
        old = before.get(name)
        new = after.get(name)
        if old is not None and new is not None and float(new) < float(old):
            drops.append({"score": name, "before": old, "after": new, "reason": "decreased"})
    return drops


def _task_sort_key(task_id: str):
    return (0, int(task_id)) if task_id.isdigit() else (1, task_id)
