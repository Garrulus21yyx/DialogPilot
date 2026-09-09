"""Evidence-first failure analysis for saved tau3 evaluation runs.

The analyzer deliberately separates observed violations from causal hypotheses.
It can prove action mismatches and typed runtime failures from local artifacts, but
it does not promote a likely component owner to a verified root cause.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence


class FindingLevel(str, Enum):
    OBSERVED = "OBSERVED"
    SUPPORTED = "SUPPORTED"
    VERIFIED = "VERIFIED"


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    source: str
    locator: str
    summary: str
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    code: str
    layer: str
    level: FindingLevel
    summary: str
    owner_candidate: str | None
    evidence_ids: tuple[str, ...]
    missing_evidence: tuple[str, ...] = ()
    next_probe: str | None = None
    impact: str = "UNKNOWN"


_WRITE_PREFIXES = (
    "cancel_", "exchange_", "modify_", "return_", "issue_", "create_",
    "update_", "delete_", "transfer_", "send_",
)
_REVISION_PATTERNS = (
    re.compile(r"\b(?:only|just)\b", re.IGNORECASE),
    re.compile(r"\b(?:skip|hold off|instead|changed my mind|never mind)\b", re.IGNORECASE),
    re.compile(r"\b(?:don't|do not|no longer|取消|不要|只要|改成|先不)\b", re.IGNORECASE),
)
_AFFIRMATIVE_PATTERN = re.compile(
    r"\b(?:yes|approve|proceed|go ahead|sounds good|that's right|all (?:three|four|five))\b",
    re.IGNORECASE,
)


def analyze_run(run_dir: Path) -> Mapping[str, Any]:
    """Analyze every complete task result in one saved tau3 run directory."""
    run_dir = run_dir.resolve()
    manifest = _load_json(run_dir / "manifest.json", default={})
    task_paths = sorted(
        path for path in run_dir.glob("task-*.json")
        if not any(part in path.name for part in ("-trajectory", "-partial-trajectory", "-user-state"))
    )
    tasks = [analyze_task(path, _trajectory_path(path)) for path in task_paths]
    counts = Counter(
        finding["code"]
        for task in tasks
        for finding in task["findings"]
        if finding["layer"] in {"violation", "mechanism"}
    )
    unbound = _unbound_run_errors(run_dir / "application-errors.log")
    return {
        "schema_version": "dialogpilot-tau3-rca-v1",
        "run": {
            "path": str(run_dir),
            "status": manifest.get("status"),
            "started_at": manifest.get("started_at"),
            "finished_at": manifest.get("finished_at"),
            "project_commit": manifest.get("project_commit"),
            "tracked_worktree_dirty": manifest.get("tracked_worktree_dirty"),
            "task_count": len(tasks),
        },
        "finding_counts": dict(sorted(counts.items())),
        "tasks": tasks,
        "unbound_run_evidence": unbound,
        "limitations": [
            "Batch-level log lines are not assigned to a task without a task, turn, or trace identifier.",
            "SUPPORTED hypotheses require the listed probe before they can become verified root causes.",
            "Visible user assent does not prove consistency with the simulator's hidden goal.",
        ],
    }


def analyze_task(result_path: Path, trajectory_path: Path | None = None) -> Mapping[str, Any]:
    result = _load_json(result_path)
    trajectory = _load_json(trajectory_path, default={}) if trajectory_path else {}
    task_id = str(result.get("task_id", _task_id(result_path)))
    evidence: list[Evidence] = []
    findings: list[Finding] = []

    expected_actions = _expected_actions(result)
    actual_actions = _actual_actions(trajectory)
    failed_expected = [action for action in expected_actions if not action["action_match"]]
    failed_writes = [action for action in failed_expected if action["tool_type"] == "write"]
    actual_writes = [action for action in actual_actions if _is_write(action["name"])]

    if result.get("evaluation_errors"):
        evidence.append(Evidence(
            "evaluation-errors", result_path.name, "$.evaluation_errors",
            "One or more official evaluators did not produce a score.",
            {"evaluation_types": sorted(result["evaluation_errors"])},
        ))
        findings.append(Finding(
            "EVALUATOR_DEPENDENCY_FAILURE", "evaluation", FindingLevel.VERIFIED,
            "The affected official score is unavailable because its evaluator failed.",
            "evaluation_runtime", ("evaluation-errors",), impact="EVALUATION_ONLY",
        ))

    provider_error = _provider_error(result)
    if provider_error:
        evidence.append(Evidence(
            "provider-error", result_path.name, "$.exception_chain|$.error",
            "The task was interrupted by a model/provider failure.", provider_error,
        ))
        findings.append(Finding(
            "PROVIDER_DEPENDENCY_FAILURE", "environment", FindingLevel.VERIFIED,
            "No business outcome can be inferred beyond the saved partial trajectory.",
            "model_provider", ("provider-error",), impact="RUN_BLOCKING",
        ))

    if failed_expected:
        evidence.append(Evidence(
            "official-action-failures", result_path.name, "$.action.action_checks",
            "The official action evaluator found unmatched expected actions.",
            {"actions": [_action_view(action) for action in failed_expected]},
        ))
        findings.append(Finding(
            "OFFICIAL_ACTION_MISMATCH", "violation", FindingLevel.OBSERVED,
            "The executed action trajectory does not match the official reference.",
            None, ("official-action-failures",),
            ("Determine whether the reference action is a required business invariant or one valid trajectory.",),
            impact="REFERENCE_DEVIATION",
        ))

    for index, expected in enumerate(failed_writes):
        expected_action = expected["action"]
        same_tool = [actual for actual in actual_writes if actual["name"] == expected_action["name"]]
        evidence_id = f"expected-write-{index}"
        if not same_tool:
            evidence.append(Evidence(
                evidence_id, result_path.name, "$.action.action_checks",
                f"Expected write {expected_action['name']} has no matching executed tool call.",
                {"expected": expected_action, "actual_write_tools": [item["name"] for item in actual_writes]},
            ))
            findings.append(Finding(
                "MISSING_REQUIRED_WRITE", "mechanism", FindingLevel.VERIFIED,
                "The required state-changing operation was not executed.",
                "execution_pipeline", (evidence_id,),
                ("Locate the earliest upstream step where the prepared or approved action disappeared.",),
                "Replay the approval-to-tool boundary with the same prepared action and context envelope.",
                impact="TASK_BLOCKING",
            ))
        else:
            best = min(same_tool, key=lambda actual: len(_argument_diff(expected_action["arguments"], actual["arguments"])))
            diff = _argument_diff(expected_action["arguments"], best["arguments"])
            evidence.append(Evidence(
                evidence_id, trajectory_path.name if trajectory_path else result_path.name,
                f"$.messages[{best['message_index']}].tool_calls[{best['tool_index']}]",
                f"Executed {best['name']} with arguments that differ from the reference.",
                {"expected": expected_action["arguments"], "actual": best["arguments"], "diff": diff},
            ))
            findings.append(Finding(
                "WRITE_ARGUMENT_MISMATCH", "mechanism", FindingLevel.VERIFIED,
                "A write occurred, but its argument set differs from the expected state transition.",
                "domain_argument_selection", (evidence_id,),
                ("Check whether the visible user approved the executed argument set and whether the hidden goal stayed consistent.",),
                "Compare hidden target, assistant proposal, user confirmation, and committed tool arguments as four separate facts.",
                impact="OUTCOME_DEVIATION",
            ))

    context_hits = _find_scalar_matches(result, "CONTEXT_BUDGET_EXCEEDED")
    if context_hits:
        evidence.append(Evidence(
            "context-budget-errors", result_path.name, context_hits[0],
            "The task trace contains a typed context-budget failure.",
            {"occurrences": len(context_hits), "locators": context_hits[:10]},
        ))
        findings.append(Finding(
            "CONTEXT_BUDGET_EXECUTION_FAILURE", "mechanism", FindingLevel.VERIFIED,
            "A model-bound execution step was rejected by the context budget contract.",
            "context_admission", ("context-budget-errors",),
            ("Token components and the producer responsible for retained context are not present in this artifact.",),
            "Replay the failing invocation with per-component token accounting and compare pre/post-compaction input.",
            impact="RECOVERED" if _business_checks_pass(result) else "PRESENT_DURING_FAILURE",
        ))

    citation_hits = _find_scalar_matches(result, "citation_validation")
    if citation_hits:
        evidence.append(Evidence(
            "citation-validation-errors", result_path.name, citation_hits[0],
            "Response assembly or verification failed citation validation.",
            {"occurrences": len(citation_hits), "locators": citation_hits[:10]},
        ))
        findings.append(Finding(
            "CITATION_VALIDATION_FAILURE", "mechanism", FindingLevel.VERIFIED,
            "A candidate response failed the citation contract.",
            "response_assembly", ("citation-validation-errors",),
            ("The artifact does not establish whether evidence production or citation consumption violated the contract.",),
            "Compare the candidate citation references with the admitted evidence IDs at response assembly.",
            impact="RECOVERED" if _business_checks_pass(result) else "PRESENT_DURING_FAILURE",
        ))

    messages = _messages(trajectory)
    revisions = [message for message in messages if message["role"] == "user" and _is_revision(message["content"])]
    domain_rejections = _find_scalar_matches(result.get("target_trace", []), "DOMAIN_OUTCOME_REJECTED")
    if revisions and domain_rejections and failed_writes and not actual_writes:
        evidence.append(Evidence(
            "user-scope-revisions", trajectory_path.name if trajectory_path else result_path.name,
            "$.messages", "The user narrowed or replaced the requested scope before completion.",
            {"messages": [{"index": item["index"], "content": item["content"][:240]} for item in revisions[-5:]]},
        ))
        evidence.append(Evidence(
            "domain-outcome-rejections", result_path.name, domain_rejections[0],
            "The target trace rejected a domain outcome after the scope revision.",
            {"occurrences": len(domain_rejections)},
        ))
        findings.append(Finding(
            "GOAL_REVISION_NOT_APPLIED", "hypothesis", FindingLevel.SUPPORTED,
            "A user scope revision was followed by rejected/no-write execution, consistent with stale task scope.",
            "goal_state_transition", ("user-scope-revisions", "domain-outcome-rejections"),
            ("Actor input/output, reviewer input/reason, and goal revision IDs must be joined to identify the owning boundary.",),
            "Compare goal revision IDs across resolver, planner/actor, reviewer, approval, and committed action spans.",
            impact="CAUSAL_CANDIDATE",
        ))

    if any(item.code == "WRITE_ARGUMENT_MISMATCH" for item in findings):
        assents = [message for message in messages if message["role"] == "user" and _AFFIRMATIVE_PATTERN.search(message["content"])]
        if assents:
            evidence.append(Evidence(
                "visible-user-assent", trajectory_path.name if trajectory_path else result_path.name,
                f"$.messages[{assents[-1]['index']}]",
                "The visible conversation contains assent before the mismatching write.",
                {"content": assents[-1]["content"][:240]},
            ))
            findings.append(Finding(
                "SIMULATOR_GOAL_DRIFT", "hypothesis", FindingLevel.SUPPORTED,
                "The simulator may have accepted an assistant-proposed set that conflicts with its hidden target.",
                "user_simulator", ("visible-user-assent",),
                ("The exact assistant proposal accepted by this assent has not been structurally linked to the write arguments.",),
                "Bind proposal ID, approval ID, hidden target and committed receipt; then compare their argument sets.",
                impact="CAUSAL_CANDIDATE",
            ))

    if not findings and _official_pass(result):
        findings.append(Finding(
            "PASS", "outcome", FindingLevel.OBSERVED,
            "Available official checks passed and no typed local failure was found.", None, (), impact="PASS",
        ))

    root_cause_status = "VERIFIED" if any(
        item.layer == "root_cause" and item.level == FindingLevel.VERIFIED for item in findings
    ) else "OPEN"
    return {
        "task_id": task_id,
        "result_file": result_path.name,
        "trajectory_file": trajectory_path.name if trajectory_path and trajectory_path.exists() else None,
        "artifact_sha256": {
            "result": _sha256(result_path),
            "trajectory": _sha256(trajectory_path) if trajectory_path and trajectory_path.exists() else None,
        },
        "langfuse_session_id": result.get("langfuse_session_id"),
        "outcome": _outcome(result),
        "root_cause_status": root_cause_status,
        "evidence": [asdict(item) for item in evidence],
        "findings": [_finding_dict(item) for item in findings],
    }


def _expected_actions(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [
        {
            "action": check.get("action") or {},
            "action_match": bool(check.get("action_match")),
            "tool_type": str(check.get("tool_type") or "unknown"),
        }
        for check in ((result.get("action") or {}).get("action_checks") or [])
    ]


def _actual_actions(trajectory: Any) -> list[Mapping[str, Any]]:
    actions = []
    for message in _messages(trajectory):
        raw = message["raw"]
        for tool_index, call in enumerate(raw.get("tool_calls") or []):
            actions.append({
                "name": str(call.get("name") or ""),
                "arguments": call.get("arguments") or call.get("args") or {},
                "call_id": call.get("id"),
                "message_index": message["index"],
                "tool_index": tool_index,
            })
    return actions


def _messages(trajectory: Any) -> list[Mapping[str, Any]]:
    raw_messages = trajectory.get("messages", []) if isinstance(trajectory, Mapping) else trajectory
    if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
        return []
    messages = []
    for index, raw in enumerate(raw_messages):
        if not isinstance(raw, Mapping):
            continue
        content = raw.get("content")
        messages.append({
            "index": index,
            "role": str(raw.get("role") or ""),
            "content": content if isinstance(content, str) else "",
            "raw": raw,
        })
    return messages


def _argument_diff(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> Mapping[str, Any]:
    keys = sorted(set(expected) | set(actual))
    return {
        key: {"expected": expected.get(key), "actual": actual.get(key)}
        for key in keys if expected.get(key) != actual.get(key)
    }


def _find_scalar_matches(value: Any, needle: str, path: str = "$") -> list[str]:
    matches = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            matches.extend(_find_scalar_matches(child, needle, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_find_scalar_matches(child, needle, f"{path}[{index}]"))
    elif needle.lower() in str(value).lower():
        matches.append(path)
    return matches


def _provider_error(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    text = json.dumps({
        "status": result.get("status"),
        "error_type": result.get("error_type"),
        "error": result.get("error"),
        "exception_chain": result.get("exception_chain"),
    }, default=str).lower()
    if any(marker in text for marker in ("insufficient balance", "http 402", "apistatuserror", "modelinvocationerror")):
        return {"status": result.get("status"), "error_type": result.get("error_type"), "error": result.get("error")}
    return None


def _unbound_run_errors(path: Path) -> list[Mapping[str, Any]]:
    if not path.exists():
        return []
    counts = Counter()
    for line in path.read_text(errors="replace").splitlines():
        upper = line.upper()
        if "CONTEXT_BUDGET_EXCEEDED" in upper:
            counts["CONTEXT_BUDGET_EXCEEDED"] += 1
        if "CITATION_VALIDATION" in upper:
            counts["CITATION_VALIDATION"] += 1
        if "MODELInvocationError".upper() in upper or "INSUFFICIENT BALANCE" in upper:
            counts["MODEL_PROVIDER_ERROR"] += 1
    return [
        {
            "code": code,
            "count": count,
            "source": path.name,
            "binding": "RUN_ONLY",
            "note": "Not attributed to a task because the log line lacks a stable task/turn/trace join key.",
        }
        for code, count in sorted(counts.items())
    ]


def _outcome(result: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "status": result.get("status"),
        "simulation_status": result.get("simulation_status"),
        "termination": result.get("termination"),
        "official_reward": result.get("official_reward"),
        "env_reward": (result.get("env") or {}).get("reward"),
        "action_reward": (result.get("action") or {}).get("reward"),
        "evaluation_scope": result.get("evaluation_scope"),
    }


def _official_pass(result: Mapping[str, Any]) -> bool:
    scores = [
        result.get("official_reward"),
        (result.get("env") or {}).get("reward"),
        (result.get("action") or {}).get("reward"),
    ]
    available = [score for score in scores if score is not None]
    return bool(available) and all(float(score) == 1.0 for score in available)


def _business_checks_pass(result: Mapping[str, Any]) -> bool:
    """ENV/ACTION are business checks; ALL may be unavailable with a judge outage."""
    scores = [
        (result.get("env") or {}).get("reward"),
        (result.get("action") or {}).get("reward"),
    ]
    available = [score for score in scores if score is not None]
    return bool(available) and all(float(score) == 1.0 for score in available)


def _is_write(name: str) -> bool:
    return name.startswith(_WRITE_PREFIXES)


def _is_revision(text: str) -> bool:
    return any(pattern.search(text) for pattern in _REVISION_PATTERNS)


def _action_view(item: Mapping[str, Any]) -> Mapping[str, Any]:
    action = item["action"]
    return {"name": action.get("name"), "arguments": action.get("arguments") or {}, "tool_type": item["tool_type"]}


def _finding_dict(item: Finding) -> Mapping[str, Any]:
    data = asdict(item)
    data["level"] = item.level.value
    return data


def _load_json(path: Path | None, default: Any = None) -> Any:
    if path is None or not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(path)
    return json.loads(path.read_text())


def _trajectory_path(result_path: Path) -> Path | None:
    task_id = _task_id(result_path)
    complete = result_path.with_name(f"task-{task_id}-trajectory.json")
    if complete.exists():
        return complete
    partial = result_path.with_name(f"task-{task_id}-partial-trajectory.json")
    return partial if partial.exists() else None


def _task_id(path: Path) -> str:
    return path.stem.removeprefix("task-")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
