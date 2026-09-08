"""Conversation-level decisions about stopped domain work, not tool retries."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from application.agent_result import AgentResultStatus, MissingInputSpec
from application.chat_contracts import StageObservation
from application.work_item import ControlMode


@dataclass(frozen=True)
class RecoveryResult:
    """A recovery decision is not an execution outcome or permission to retry."""

    questions: tuple[MissingInputSpec, ...]
    observation: StageObservation
    retryable: bool = False


class RecoveryDecisionUnavailable(RuntimeError):
    """The follow-up node did not decide; completed business work stays saved."""

    def __init__(self, result: RecoveryResult):
        super().__init__(str(result.observation.detail["code"]))
        self.retryable = result.retryable
        self.diagnostics = (result.observation,)


def planning_continuations(state, resolved_items=()):
    """Project pending work, not revision validity, into semantic resume choices.

    Resolver-owned typed input replaces the corresponding persisted envelope.
    Acceptance and actual checkpoint recovery remain with Policy and Runtime.
    """
    items = {(item.work_item_id, item.control): item
        for pending in (state.pending_interaction, state.pending_approval)
        if pending is not None for item in pending.suspended_work_items}
    items.update({(item.work_item_id, item.control): item for item in resolved_items})
    return tuple(item for item in items.values()
                 if item.control is not None and state.accepts(item.control))


def recovery_candidates(plan, board):
    items = {item.work_item_id: item for item in plan.items}
    return tuple(result for result in board.results
        if result.status in {AgentResultStatus.BLOCKED, AgentResultStatus.NEEDS_EVIDENCE, AgentResultStatus.RETRYABLE_FAILURE,
                             AgentResultStatus.TERMINAL_FAILURE}
        and items[result.work_item_id].control_mode is ControlMode.DELEGATED
        and not result.reason_code.startswith("UPSTREAM_NOT_SUCCESSFUL:")
        and not result.pending_action
        and not any(receipt.effect_status == "OUTCOME_UNKNOWN" for receipt in result.action_receipts))


def recovery_schema():
    return {"type": "object", "additionalProperties": False, "required": ["decisions"],
        "properties": {"decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["work_item_id", "action"], "properties": {
                "work_item_id": {"type": "string", "minLength": 1},
                "action": {"enum": ["ask_user", "finish"]},
                "question": {"type": "string", "minLength": 1}},
            "oneOf": [
                {"properties": {"action": {"const": "ask_user"}}, "required": ["question"]},
                {"properties": {"action": {"const": "finish"}}, "not": {"required": ["question"]}}]}}}}


def failure_feedback(result):
    """Read the execution-owned diagnostic record, not a compressed chat view."""
    return list(result.execution_feedback)


def recovery_payload(plan, board, candidates, *, current_message, conversation_context):
    """Preserve decision evidence without copying the domain's working transcript."""
    items = {item.work_item_id: item for item in plan.items}

    def outcome(item, result):
        identity = {"work_item_id": item.work_item_id, "owner_agent": item.owner_agent,
            "control": asdict(item.control) if item.control else None,
            "objective": item.objective}
        if result is None:
            return {**identity, "status": None}
        return {
            **identity, "status": result.status.value,
            "reason_code": result.reason_code,
            "retained_facts": [{**asdict(fact), "observed_at": fact.observed_at.isoformat(),
                "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
                "observation_started_at": fact.observation_started_at.isoformat()
                    if fact.observation_started_at else None} for fact in result.facts],
            "action_receipts": [asdict(receipt) for receipt in result.action_receipts],
            "retained_evidence_refs": list(result.evidence_refs),
            "pending_action": ({"action_ref": result.pending_action.action_ref,
                "arguments": {arg.name: arg.value for arg in result.pending_action.arguments},
                "status": "AWAITING_APPROVAL_NOT_EXECUTED"} if result.pending_action else None),
        }

    current = {(items[result.work_item_id].control, result.work_item_id) for result in candidates}
    return {"current_message": current_message, "conversation_context": conversation_context,
        "stopped_tasks": [{
            **outcome(items[result.work_item_id], result),
            "accepted_arguments": {arg.name: arg.value for arg in items[result.work_item_id].arguments},
            "allowed_capabilities": {"tools": list(items[result.work_item_id].allowed_tools),
                "skills": list(items[result.work_item_id].allowed_skills),
                "actions": list(items[result.work_item_id].allowed_actions)},
            "dependencies": list(items[result.work_item_id].dependencies),
            "retryable": result.retryable,
            "domain_explanation": result.candidate_response,
            "execution_feedback": failure_feedback(result),
            "unresolved_evidence": [asdict(request) for request in result.requested_evidence],
        } for result in candidates],
        "other_outcomes": [outcome(item, result) for item, result in board.outcome_items
            if (item.control, item.work_item_id) not in current]}


def recovery_questions(raw, candidates):
    from jsonschema import Draft202012Validator
    Draft202012Validator(recovery_schema()).validate(raw)
    expected = {result.work_item_id for result in candidates}
    ids = [decision["work_item_id"] for decision in raw["decisions"]]
    if len(ids) != len(set(ids)) or set(ids) != expected:
        raise ValueError("recovery decisions must cover each stopped task exactly once")
    return tuple(MissingInputSpec("reply", decision["work_item_id"],
        "CONVERSATION_RECOVERY_INPUT", "string", decision["question"])
        for decision in raw["decisions"] if decision["action"] == "ask_user")
