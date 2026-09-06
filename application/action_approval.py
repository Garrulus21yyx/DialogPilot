"""Bind a prepared domain action to the conversation's existing approval state."""
from datetime import datetime, timedelta, timezone

from application.conversation_state import (
    ConversationStateConflict, PendingApprovalState, WorkstreamState, WorkstreamStatus,
)


def bind_action_approval(state, plan, board, registry, checkpoint_thread_id):
    proposed = tuple(result for result in board.results if result.pending_action is not None)
    if not proposed:
        return state
    if state.pending_approval or state.pending_interaction:
        raise ConversationStateConflict("one pending action decision is supported per conversation")
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
            and any(outcome.work_item_id == work.work_item_id
                    and outcome.status.value not in {"SUCCEEDED", "CANCELLED", "SUPERSEDED"}
                    for outcome in board.results)
        )),
    ), new_workstream=stream)
