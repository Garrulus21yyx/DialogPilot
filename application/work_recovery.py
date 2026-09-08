"""Conversation-level decisions about stopped domain work, not tool retries."""
from __future__ import annotations

from application.agent_result import AgentResultStatus, MissingInputSpec
from application.work_item import ControlMode


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
