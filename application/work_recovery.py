"""Conversation-level decisions about stopped domain work, not tool retries."""
from __future__ import annotations

from application.agent_result import AgentResultStatus, MissingInputSpec
from application.work_item import ControlMode


def repair_input_plan(prepared, managed, rejected_ids, *, registry, policy, compiler):
    """Revise only rejected domain objectives; retain unchanged work in the DAG."""
    from dataclasses import replace
    from application.target_understanding import StateBoundTargetUnderstanding
    from application.turn_planning import TurnProposal, ProposalDisposition
    from application.work_item import WorkPlan
    rejected = set(rejected_ids)
    bound = {spec.target_work_item_id for spec in managed.interaction_questions}
    items = managed.plan.work.items
    results = {result.work_item_id: result for result in managed.board.results}
    selected = tuple(item for item in items if item.work_item_id in rejected)
    if not rejected or not rejected.issubset(bound) or len(selected) != len(rejected):
        raise ValueError("interaction rejection does not bind current requested inputs")
    if any(item.control_mode is not ControlMode.DELEGATED
           or results[item.work_item_id].pending_action is not None
           or results[item.work_item_id].action_receipts for item in selected):
        raise ValueError("interaction repair requires non-writing delegated work")
    commands = tuple(replace(StateBoundTargetUnderstanding._resume_command(index, item),
        command_id=f"repair-input-{index}") for index, item in enumerate(selected, 1))
    validated = policy.accept(TurnProposal(ProposalDisposition.RESOLVED, commands,
        "REJECTED_INPUT_REPAIR"), prepared.state, registry)
    compiled = compiler.compile(validated, prepared.state, registry, prepared.invocation)
    replacements = dict(zip((item.work_item_id for item in selected), compiled.work.items))
    remap = {old: item.work_item_id for old, item in replacements.items()}
    repaired = tuple(replace(replacements.get(item.work_item_id, item),
        dependencies=tuple(remap.get(dependency, dependency) for dependency in item.dependencies))
        for item in items)
    return replace(managed.plan, state_fingerprint=compiled.state_fingerprint, work=WorkPlan(repaired,
        remap.get(managed.plan.work.primary_work_item_id, managed.plan.work.primary_work_item_id))), compiled.work.items


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
