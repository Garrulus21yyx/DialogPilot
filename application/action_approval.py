"""Bind a prepared domain action to the conversation's existing approval state."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from application.conversation_state import (
    ConversationStateConflict, PendingApprovalState, WorkstreamState, WorkstreamStatus,
)


ACTION_INTERACTION_CONTRACT = """Action interaction has three stages with distinct owners:
1. Resolve the requested targets from the user's constraints and business evidence.
   Ask only for an unresolved value or an actual choice the policy requires the user
   to supply. A stored option is not a user selection when policy requires one.
   A uniquely resolved target set does not need a separate completeness confirmation.
2. With those values available, prepare the proposal without executing it.
   Use an available preparation action or delegate the complete business objective
   with proposal permission. Do not ask for execution permission before preparation.
   An information-only request permits investigation, not preparing a business change.
3. Runtime presents the complete proposal and obtains one execution approval,
   including policy-required confirmation of targets, full item list, consequences
   and payment terms. Such pre-execution confirmations belong here, not stage 1.
   Ask execution approval only for the prepared action selected for presentation,
   not for other requested changes that are merely discussed or still queued.
A missing-input question must ask only for its missing value/choice; do not add
confirmation of already resolved targets or permission to proceed. Prior assent
without a matching prepared action is user intent, not a runtime approval grant.
This preserves required user choices without collecting execution approval twice.
"""


def partition_approval_revision(pending, affected_controls):
    """Separate invalidated work and unaffected continuations of one wait.

    Dependencies of a changed objective require a new plan, not implicit reuse.
    The checkpoint remains the owner of previously completed results.
    """
    binding = pending.origin_control if pending else None
    if binding is None or binding.control_id not in affected_controls:
        return (), ()
    suspended = pending.suspended_work_items
    excluded = {item.work_item_id for item in suspended
                if item.control and item.control.control_id in affected_controls}
    excluded.update(value for value in (pending.origin_work_item_id, pending.work_item_id) if value)
    while True:
        expanded = excluded | {item.work_item_id for item in suspended
                               if excluded.intersection(item.dependencies)}
        if expanded == excluded:
            break
        excluded = expanded
    return (tuple(item for item in suspended if item.work_item_id in excluded),
            tuple(item for item in suspended if item.work_item_id not in excluded))


def bind_action_approval(state, plan, board, registry, checkpoint_thread_id):
    proposed = tuple(result for result in board.results if result.pending_action is not None)
    if len(proposed) > 1:
        raise ConversationStateConflict("action-capable workers must be serialized before approval")
    finished = {result.work_item_id for result in board.results
                if result.status.value in {"SUCCEEDED", "CANCELLED", "SUPERSEDED"}}
    waiting = {result.work_item_id for result in board.results if result.status.value == "NEEDS_USER_INPUT"}
    if state.pending_interaction:
        waiting.update(work.work_item_id for work in plan.work.items if any(
            work == original or work.control is not None and work.control == original.control
            for original in state.pending_interaction.suspended_work_items))
    while True:
        expanded = waiting | {work.work_item_id for work in plan.work.items if waiting.intersection(work.dependencies)}
        if expanded == waiting:
            break
        waiting = expanded
    if state.pending_approval:
        # A prepared explicit action already owns this turn's decision. Queue
        # unfinished domain objectives behind it, without replacing its grant.
        pending = state.pending_approval
        if pending.checkpoint_thread_id != checkpoint_thread_id or not any(
            work.control is not None and work.control == pending.origin_control for work in plan.work.items
        ):
            return state
        additions = tuple(work for work in plan.work.items
                          if work.work_item_id not in finished | waiting and not any(
                              work == original or work.control is not None and work.control == original.control
                              for original in pending.suspended_work_items))
        if not additions:
            return state
        return replace(state, version=state.version + 1, pending_approval=replace(
            pending, suspended_work_items=(*pending.suspended_work_items, *additions)))
    if not proposed:
        return state
    result = proposed[0]
    parent = next(item for item in plan.work.items if item.work_item_id == result.work_item_id)
    action = result.pending_action
    definition = registry.action(action.action_ref)
    if (action.action_ref not in parent.allowed_actions
            or definition.owner_agent != parent.owner_agent
            or action.registry_fingerprint != registry.fingerprint
            or action.control != parent.control
            or action.allowed_tools != (*definition.allowed_tool_ids, definition.reconciliation.tool_id)
            or action.requirement_ids != definition.requirement_ids
            or action.risk != definition.risk
            or action.flow_ref != definition.flow_ref
            or action.expected_output_schema != definition.receipt_schema_version
            or action.verification_profile != definition.verification_profile
            or action.reconciliation != definition.reconciliation
            or action.approval_policy != definition.approval_policy):
        raise ConversationStateConflict("prepared action differs from its registered envelope")
    stream_id = "action-workstream:" + action.operation_key
    stream = WorkstreamState(
        stream_id, parent.owner_agent, definition.ref, "PREPARED",
        WorkstreamStatus.WAITING_APPROVAL, 1, action.arguments, definition.flow_ref,
    )
    return state.wait_for_approval(PendingApprovalState(
        action.approval_binding, 1, stream_id, action.work_item_id, definition.ref,
        action.operation_key, action.aggregate_ref, action.target_entity_version,
        (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        action.arguments, checkpoint_thread_id, action.argument_bindings,
        suspended_work_items=(parent, *(
            work for work in plan.work.items if work.work_item_id != parent.work_item_id
            and work.work_item_id not in finished | waiting
        )),
        origin_work_item_id=parent.work_item_id,
        control=parent.control,
    ), new_workstream=stream)
