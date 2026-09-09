"""Reviewable regression contracts generated from tau3 RCA findings."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping, Sequence


_REGRESSION_IMPACTS = {"TASK_BLOCKING", "OUTCOME_DEVIATION"}


def generate_candidates(report: Mapping[str, Any]) -> Mapping[str, Any]:
    candidates = []
    for task in report.get("tasks", []):
        blocking_codes = sorted({
            finding["code"] for finding in task.get("findings", [])
            if finding.get("impact") in _REGRESSION_IMPACTS
        })
        if not blocking_codes:
            continue
        source = {
            "task_id": str(task["task_id"]),
            "result_sha256": task["artifact_sha256"]["result"],
            "trajectory_sha256": task["artifact_sha256"].get("trajectory"),
            "project_commit": report.get("run", {}).get("project_commit"),
        }
        suggested_contract = {
            "forbidden_findings": blocking_codes,
            "minimum_scores": {"env_reward": 1.0, "action_reward": 1.0},
        }
        candidate_id = "tau3-" + hashlib.sha256(json.dumps({
            "source": source, "failure_codes": blocking_codes,
            "suggested_contract": suggested_contract,
        }, sort_keys=True).encode()).hexdigest()[:16]
        candidates.append({
            "candidate_id": candidate_id,
            "status": "CANDIDATE",
            "source": source,
            "failure_codes": blocking_codes,
            "suggested_contract": suggested_contract,
            "review_required": [
                "Confirm that the official expected action is a business invariant.",
                "Confirm the expected state and side effects without exposing hidden targets to the agent.",
                "Add at least one scope-preserving variant before using this case as general closure evidence.",
            ],
        })
    return {
        "schema_version": "dialogpilot-tau3-regression-candidates-v1",
        "source_report_version": report.get("schema_version"),
        "candidates": candidates,
    }


def promote_candidates(
    candidates: Mapping[str, Any], reviews: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Promote only explicitly approved, source-bound contracts."""
    review_by_id = {str(item["candidate_id"]): item for item in reviews.get("reviews", [])}
    promoted = []
    rejected = []
    for candidate in candidates.get("candidates", []):
        review = review_by_id.get(str(candidate["candidate_id"]))
        if not review or review.get("decision") not in {"APPROVE", "REJECT"}:
            continue
        if review["decision"] == "REJECT":
            rejected.append({
                "candidate_id": candidate["candidate_id"],
                "reason": review.get("reason") or "Rejected during contract review.",
            })
            continue
        _validate_approval(candidate, review)
        contract = deepcopy(review.get("contract") or candidate["suggested_contract"])
        promoted.append({
            "regression_id": candidate["candidate_id"],
            "task_id": candidate["source"]["task_id"],
            "status": "ACTIVE",
            "source": candidate["source"],
            "contract": contract,
            "review": {
                "reviewer": review["reviewer"],
                "reviewed_at": review["reviewed_at"],
                "reason": review.get("reason"),
            },
        })
    return {
        "schema_version": "dialogpilot-tau3-regression-suite-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regressions": promoted,
        "rejected": rejected,
    }


def evaluate_regressions(
    report: Mapping[str, Any], suite: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    tasks = {str(task["task_id"]): task for task in report.get("tasks", [])}
    results = []
    for regression in suite.get("regressions", []):
        task = tasks.get(str(regression["task_id"]))
        if task is None:
            results.append(_regression_result(regression, "UNAVAILABLE", ["task result is absent"]))
            continue
        failures = []
        finding_codes = {item["code"] for item in task.get("findings", [])}
        for code in regression["contract"].get("forbidden_findings", []):
            if code in finding_codes:
                failures.append(f"forbidden finding remains: {code}")
        outcome = task.get("outcome", {})
        for score, minimum in regression["contract"].get("minimum_scores", {}).items():
            value = outcome.get(score)
            if value is None:
                failures.append(f"required score unavailable: {score}")
            elif float(value) < float(minimum):
                failures.append(f"{score}={value} is below {minimum}")
        results.append(_regression_result(regression, "FAIL" if failures else "PASS", failures))
    return results


def _validate_approval(candidate: Mapping[str, Any], review: Mapping[str, Any]) -> None:
    missing = [field for field in ("reviewer", "reviewed_at") if not review.get(field)]
    if missing:
        raise ValueError(f"approved candidate {candidate['candidate_id']} missing review fields: {','.join(missing)}")
    contract = review.get("contract") or candidate.get("suggested_contract")
    if not isinstance(contract, Mapping) or not contract:
        raise ValueError(f"approved candidate {candidate['candidate_id']} has no contract")
    if not contract.get("forbidden_findings") and not contract.get("minimum_scores"):
        raise ValueError(f"approved candidate {candidate['candidate_id']} has an empty contract")


def _regression_result(
    regression: Mapping[str, Any], status: str, failures: Sequence[str]
) -> Mapping[str, Any]:
    return {
        "regression_id": regression["regression_id"],
        "task_id": regression["task_id"],
        "status": status,
        "failures": list(failures),
    }
