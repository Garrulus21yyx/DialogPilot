"""Normalize tau3 artifacts into required-transition paths and first divergences."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


PHASES = (
    "OBJECTIVE", "PLAN", "WORK_ITEM", "ACTION_PREPARED", "APPROVAL",
    "EXECUTION", "RECEIPT", "STATE", "ANSWER",
)


def analyze_required_transitions(
    result: Mapping[str, Any], *, required_writes: Sequence[Mapping[str, Any]],
    actual_writes: Sequence[Mapping[str, Any]],
    planning_failures: Sequence[Mapping[str, Any]],
    conversation_messages: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, Any]:
    """Build one bounded path per unmatched required state-changing action."""
    paths = []
    evidence = []
    hypotheses = []
    projection = _checkpoint_facts(result)
    approval_ids = set(projection["approval_ids"])
    approval_requests = [
        request for request in _approval_requests(result)
        if request.get("signal_id") in approval_ids
    ]
    for index, expected in enumerate(required_writes):
        action = expected.get("action") or {}
        name = str(action.get("name") or "")
        action_match = bool(expected.get("action_match"))
        failure_evidence_id = expected.get("failure_evidence_id")
        same_tool = [item for item in actual_writes if item.get("name") == name]
        pending = [item for item in projection["pending_actions"] if name in item["allowed_tools"]]
        failure_turn = _first_failure_after(
            planning_failures,
            min((item["turn"] for item in approval_requests), default=None),
        )
        approval_input = _turn_input(result, failure_turn.get("turn")) if failure_turn else None
        conversation_window = _conversation_window(conversation_messages, approval_input)
        evidence_id = f"transition-{index}-lineage"
        evidence.append({
            "evidence_id": evidence_id,
            "locator": "$.checkpoint_projection|$.target_trace|$.action.action_checks",
            "summary": f"Normalized required-transition evidence for {name}.",
            "data": {
                "required_action": name,
                "pending_actions": pending[:5],
                "approval_requests": approval_requests[-5:],
                "approval_ids": projection["approval_ids"][:10],
                "operation_keys": projection["operation_keys"][:10],
                "receipt_ids": projection["receipt_ids"][:10],
                "same_tool_call_count": len(same_tool),
                "planning_failure": failure_turn,
                "approval_input": approval_input,
                "conversation_window": conversation_window,
            },
        })
        transitions = _transition_path(
            result, name=name, pending=pending, approval_requests=approval_requests,
            approval_input=approval_input, same_tool=same_tool,
            receipt_ids=projection["receipt_ids"], failure_turn=failure_turn,
            action_match=action_match, projection_available=projection["available"],
        )
        divergence = _first_divergence(
            result,
            name=name, pending=pending, approval_requests=approval_requests,
            approval_input=approval_input, same_tool=same_tool, failure_turn=failure_turn,
            action_match=action_match,
        )
        paths.append({
            "required_action": name,
            "phases": list(PHASES),
            "transitions": transitions,
            "first_divergence": divergence,
            "evidence_ids": [
                item for item in (evidence_id, failure_evidence_id) if item
            ],
        })
        if divergence:
            hypotheses.append({
                "code": divergence["code"],
                "layer": "causal_candidate",
                "summary": divergence["summary"],
                "owner_candidate": divergence["owner_candidate"],
                "evidence_ids": (
                    evidence_id,
                    *((failure_evidence_id,) if failure_evidence_id else ()),
                    *(('planning-stage-failures',) if failure_turn else ()),
                ),
                "missing_evidence": tuple(divergence["missing_evidence"]),
                "next_probe": divergence["next_probe"],
            })
            semantic = _semantic_hypothesis(
                divergence, name=name, approval_input=approval_input,
                evidence_id=evidence_id,
            )
            if semantic:
                hypotheses.append(semantic)
    if not any(path["first_divergence"] for path in paths):
        outcome_path = _outcome_divergence_path(result)
        if outcome_path:
            evidence.append({
                "evidence_id": "transition-outcome",
                "locator": "$.official_reward|$.env.reward|$.action.reward",
                "summary": "Official outcome scores identify the terminal evaluation boundary.",
                "data": {
                    "official_reward": result.get("official_reward"),
                    "env_reward": (result.get("env") or {}).get("reward"),
                    "action_reward": (result.get("action") or {}).get("reward"),
                },
            })
            outcome_path["evidence_ids"] = ["transition-outcome"]
            outcome_path["hypothesis"]["evidence_ids"] = ("transition-outcome",)
            paths.append(outcome_path)
            hypotheses.append(outcome_path["hypothesis"])
            del outcome_path["hypothesis"]
    divergences = [path["first_divergence"] for path in paths if path["first_divergence"]]
    first = min(divergences, key=lambda item: PHASES.index(item["phase"]), default=None)
    return {
        "schema_version": "tau3-required-transitions-v1",
        "phase_order": list(PHASES),
        "paths": paths,
        "first_divergence": first,
        "evidence": evidence,
        "hypotheses": hypotheses,
    }


def _transition_path(
    result: Mapping[str, Any], *, name: str, pending: Sequence[Mapping[str, Any]],
    approval_requests: Sequence[Mapping[str, Any]], approval_input: str | None,
    same_tool: Sequence[Mapping[str, Any]], receipt_ids: Sequence[str],
    failure_turn: Mapping[str, Any] | None, action_match: bool,
    projection_available: bool,
) -> list[Mapping[str, Any]]:
    env_reward = (result.get("env") or {}).get("reward")
    prepared = bool(pending)
    approval_required = prepared and bool(approval_requests)
    return [
        _step("OBJECTIVE", "SATISFIED", "Official evaluator requires the write."),
        _step("PLAN", "SATISFIED" if prepared else "UNKNOWN",
              "A later pending action implies upstream planning." if prepared else "No matching prepared action is visible."),
        _step("WORK_ITEM", "SATISFIED" if prepared else "UNKNOWN",
              "Checkpoint lineage contains the pending work item." if prepared else "No matching work item is linked."),
        _step("ACTION_PREPARED", "SATISFIED" if prepared else "FAILED",
              f"Checkpoint contains pending action {name}." if prepared else f"No pending action exposes {name}."),
        _step("APPROVAL", (
            "SEMANTIC_PENDING" if approval_required and approval_input
            else "WAITING" if approval_required else "NOT_APPLICABLE" if not prepared else "UNKNOWN"
        ), "Approval text requires bounded semantic evaluation." if approval_input else
            "No approval response is linked to the pending action."),
        _step("EXECUTION", "SATISFIED" if action_match else "FAILED",
              "The required write transition matched." if action_match else "The required write transition did not match.",
              failure=failure_turn),
        _step("RECEIPT", "SATISFIED" if receipt_ids else "FAILED" if projection_available else "UNKNOWN",
              "A committed receipt is projected." if receipt_ids else "No committed receipt is projected."),
        _step("STATE", "SATISFIED" if env_reward == 1 else "FAILED" if env_reward == 0 else "UNKNOWN",
              f"ENV reward is {env_reward!r}."),
        _step("ANSWER", "SATISFIED" if result.get("official_reward") == 1 else "UNKNOWN",
              "Answer quality is evaluated separately from state transitions."),
    ]


def _first_divergence(
    result: Mapping[str, Any], *, name: str,
    pending: Sequence[Mapping[str, Any]], approval_requests: Sequence[Mapping[str, Any]],
    approval_input: str | None, same_tool: Sequence[Mapping[str, Any]],
    failure_turn: Mapping[str, Any] | None, action_match: bool,
) -> Mapping[str, Any] | None:
    env_reward = (result.get("env") or {}).get("reward")
    if action_match and env_reward == 0:
        return {
            "phase": "STATE", "code": "STATE_OUTCOME_MISMATCH",
            "owner_candidate": "domain_state_transition",
            "summary": f"The required {name} action matched, but the final database state did not.",
            "missing_evidence": ["Link the committed receipt to the failed state assertion and authoritative entity version."],
            "next_probe": "Compare receipt postconditions with the final state diff and reconciliation result.",
        }
    if action_match:
        return None
    if same_tool:
        return {
            "phase": "EXECUTION", "code": "WRITE_ARGUMENT_SELECTION_DIVERGED",
            "owner_candidate": "domain_argument_selection",
            "summary": f"The required action {name} was called but did not match the required transition.",
            "missing_evidence": ["Compare supported alternative trajectories before treating the reference arguments as unique."],
            "next_probe": "Compare user-approved arguments, tool arguments, and final database state.",
        }
    if pending and approval_requests and approval_input and failure_turn:
        code = str(failure_turn.get("code") or "")
        owner = "context_admission" if code == "CONTEXT_BUDGET_EXCEEDED" else "conversation_planning"
        detail = failure_turn.get("detail") or {}
        binding_blocked = (
            code == "CONTEXT_BUDGET_EXCEEDED"
            and detail.get("approval_binding_status") == "SEMANTIC_RESOLUTION_NOT_REACHED"
        )
        return {
            "phase": "APPROVAL", "code": (
                "APPROVAL_RESOLUTION_BLOCKED_BY_CONTEXT_ADMISSION"
                if binding_blocked else "ACTION_RESUME_BLOCKED"
            ),
            "owner_candidate": owner,
            "summary": (
                f"Context admission rejected the response turn before semantic approval resolution for prepared {name}."
                if binding_blocked else
                f"A prepared {name} action reached approval, but the response turn failed before execution."
            ),
            "missing_evidence": ([
                "Confirm that the user response accepts the projected approval scope.",
            ] if binding_blocked else [
                "Confirm that the user response accepts the projected approval scope.",
                "Confirm the deterministic approval binding before the failing model boundary.",
            ]),
            "next_probe": (
                "Inspect the recorded context token components and repair the owning admission boundary."
                if binding_blocked else
                "Join approval ID, response decision, resume thread and failing stage; replay that boundary."
            ),
        }
    if pending:
        return {
            "phase": "APPROVAL" if approval_requests else "EXECUTION",
            "code": "PREPARED_ACTION_NOT_COMMITTED",
            "owner_candidate": "approval_execution_boundary",
            "summary": f"The {name} action was prepared but has no matching tool call or receipt.",
            "missing_evidence": ["A terminal approval or execution transition is not present."],
            "next_probe": "Inspect the checkpoint delta after pending-action creation for approval, cancellation or execution.",
        }
    if failure_turn:
        return {
            "phase": "PLAN", "code": "ACTION_NOT_PREPARED_AFTER_PLANNING_FAILURE",
            "owner_candidate": "conversation_planning",
            "summary": f"Planning failed and the required {name} action never reached preparation.",
            "missing_evidence": ["Confirm that the failing turn semantically requested the required action."],
            "next_probe": "Replay the failing planning boundary with the same bounded conversation state.",
        }
    return {
        "phase": "ACTION_PREPARED", "code": "REQUIRED_ACTION_NOT_PREPARED",
        "owner_candidate": "task_planning",
        "summary": f"The required {name} action has no prepared or executed representation.",
        "missing_evidence": ["No typed upstream failure identifies why preparation was omitted."],
        "next_probe": "Evaluate the plan and work-item output for required-action coverage.",
    }


def _checkpoint_facts(result: Mapping[str, Any]) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "available": False, "pending_actions": [], "approval_ids": [],
        "operation_keys": [], "receipt_ids": [],
    }
    projection = result.get("checkpoint_projection") or {}
    if projection.get("status") != "AVAILABLE":
        return facts
    facts["available"] = True
    seen_pending = set()
    for snapshot_index, snapshot in enumerate(projection.get("snapshots") or []):
        if not isinstance(snapshot, Mapping):
            continue
        for item in snapshot.get("lineage") or []:
            if not isinstance(item, Mapping):
                continue
            field, value = item.get("field"), item.get("value")
            path = str(item.get("path") or "")
            if field == "allowed_tools" and ".pending_action." in path and isinstance(value, list):
                key = (snapshot.get("thread_id"), path, tuple(str(tool) for tool in value))
                if key not in seen_pending:
                    seen_pending.add(key)
                    facts["pending_actions"].append({
                        "snapshot_index": snapshot_index, "sequence": snapshot.get("sequence"),
                        "thread_id": snapshot.get("thread_id"), "path": path,
                        "allowed_tools": [str(tool) for tool in value],
                    })
            elif field == "approval_id" and value:
                facts["approval_ids"].append(str(value))
            elif field == "operation_key" and value:
                facts["operation_keys"].append(str(value))
            elif field in {"receipt_id", "receipt_ref"} and value:
                facts["receipt_ids"].append(str(value))
            elif field == "committed_receipt_refs" and isinstance(value, list):
                facts["receipt_ids"].extend(str(receipt) for receipt in value)
    for key in ("approval_ids", "operation_keys", "receipt_ids"):
        facts[key] = list(dict.fromkeys(facts[key]))
    return facts


def _outcome_divergence_path(result: Mapping[str, Any]) -> Mapping[str, Any] | None:
    env_reward = (result.get("env") or {}).get("reward")
    action_reward = (result.get("action") or {}).get("reward")
    official = result.get("official_reward")
    if env_reward == 0:
        divergence = {
            "phase": "STATE", "code": "STATE_OUTCOME_MISMATCH",
            "owner_candidate": "domain_state_transition",
            "summary": "The authoritative final environment state does not match the task contract.",
            "missing_evidence": ["No required write path identifies the earlier state transition responsible."],
            "next_probe": "Compare initial/final state diff with committed receipts and required state assertions.",
        }
    elif env_reward == 1 and action_reward in (None, 1) and official == 0:
        divergence = {
            "phase": "ANSWER", "code": "ANSWER_OR_COMMUNICATION_MISMATCH",
            "owner_candidate": "response_assembly",
            "summary": "State and action checks pass, but the overall response contract fails.",
            "missing_evidence": ["A bounded semantic judge must identify the unsupported or omitted communication requirement."],
            "next_probe": "Judge the final answer against required communication facts and committed state.",
        }
    else:
        return None
    transitions = [
        _step(phase, "FAILED" if phase == divergence["phase"] else "UNKNOWN",
              divergence["summary"] if phase == divergence["phase"] else "Not required to locate this outcome divergence.")
        for phase in PHASES
    ]
    return {
        "required_action": None,
        "phases": list(PHASES),
        "transitions": transitions,
        "first_divergence": divergence,
        "evidence_ids": [],
        "hypothesis": {
            "code": divergence["code"], "summary": divergence["summary"],
            "layer": (
                "hypothesis" if divergence["phase"] == "ANSWER" else "causal_candidate"
            ),
            "owner_candidate": divergence["owner_candidate"], "evidence_ids": (),
            "missing_evidence": tuple(divergence["missing_evidence"]),
            "next_probe": divergence["next_probe"],
        },
    }


def _semantic_hypothesis(
    divergence: Mapping[str, Any], *, name: str, approval_input: str | None,
    evidence_id: str,
) -> Mapping[str, Any] | None:
    if divergence["code"] in {
        "ACTION_RESUME_BLOCKED", "APPROVAL_RESOLUTION_BLOCKED_BY_CONTEXT_ADMISSION",
    } and approval_input:
        return {
            "code": "APPROVAL_RESPONSE_MATCHES_PENDING_ACTION",
            "layer": "hypothesis",
            "summary": f"The response semantically approves the pending {name} action.",
            "owner_candidate": None,
            "evidence_ids": (evidence_id,),
            "missing_evidence": (
                "Compare the response with the exact pending-action scope and arguments.",
            ),
            "next_probe": "Use the bounded semantic judge over only the approval response and pending action.",
        }
    if divergence["code"] == "ACTION_NOT_PREPARED_AFTER_PLANNING_FAILURE":
        return {
            "code": "FAILED_TURN_REQUESTS_REQUIRED_ACTION",
            "layer": "hypothesis",
            "summary": f"The failed turn semantically requests the required {name} action.",
            "owner_candidate": None,
            "evidence_ids": (evidence_id,),
            "missing_evidence": ("Compare the turn text with the required action contract.",),
            "next_probe": "Use the bounded semantic judge over the failed turn and required action.",
        }
    return None


def _approval_requests(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    requests = []
    for index, record in enumerate(result.get("target_trace") or []):
        if not isinstance(record, Mapping):
            continue
        outcome = record.get("outcome") or {}
        if isinstance(outcome, Mapping) and outcome.get("kind") == "APPROVAL":
            requests.append({
                "turn": record.get("turn"), "trace_index": index,
                "signal_id": outcome.get("signal_id"),
                "workflow_run_id": outcome.get("workflow_run_id"),
            })
    return requests


def _first_failure_after(
    failures: Sequence[Mapping[str, Any]], turn: int | None,
) -> Mapping[str, Any] | None:
    candidates = [
        item for item in failures
        if isinstance(item.get("turn"), int) and (turn is None or item["turn"] > turn)
    ]
    return min(candidates, key=lambda item: item["turn"], default=None)


def _turn_input(result: Mapping[str, Any], turn: Any) -> str | None:
    for record in result.get("target_trace") or []:
        if isinstance(record, Mapping) and record.get("turn") == turn:
            text = str(record.get("input") or "").strip()
            return text[:800] if text else None
    return None


def _conversation_window(
    messages: Sequence[Mapping[str, Any]], user_input: str | None,
) -> list[Mapping[str, str]]:
    if not user_input:
        return []
    for index, message in enumerate(messages):
        if message.get("role") != "user" or str(message.get("content") or "").strip() != user_input:
            continue
        previous = next((
            candidate for candidate in reversed(messages[:index])
            if candidate.get("role") == "assistant" and str(candidate.get("content") or "").strip()
        ), None)
        window = []
        if previous:
            window.append({
                "role": "assistant", "content": str(previous["content"])[-2400:],
            })
        window.append({"role": "user", "content": user_input[:800]})
        return window
    return [{"role": "user", "content": user_input[:800]}]


def _step(
    phase: str, status: str, evidence: str, *, failure: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    result = {"phase": phase, "status": status, "evidence": evidence}
    if failure:
        result["failure"] = dict(failure)
    return result
