"""Bind a prepared domain action to the conversation's existing approval state."""
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace

from application.conversation_state import (
    ConversationStateConflict, PendingApprovalState, WorkstreamState, WorkstreamStatus,
)


def approval_presentation_due(pending, previous, *, published):
    """A saved, undelivered proposal remains presentable on conversational turns.

    Publication, not the planner's reply/work choice, owns delivery. A successfully
    presented proposal is retained context, not a new request for the same assent.
    """
    return pending is not None and (
        previous is None or (pending.approval_id, pending.version) !=
        (previous.approval_id, previous.version) or published is False)


ACTION_INTERACTION_CONTRACT = """Action interaction has three stages with distinct owners:
1. Resolve the requested targets from the user's constraints and business evidence.
   Ask only for an unresolved value or an actual choice the policy requires the user
   to supply. A stored option is not a user selection when policy requires one.
   A uniquely resolved target set does not need a separate completeness confirmation.
2. With those values available, prepare the proposal without executing it.
   Complete checks affecting action choice, compatibility or approval terms first.
   A worker segment may prepare a set of concrete, ready actions together.
   Successful preparation ends that
   segment, not the overall objective. The proposal and unfinished work return to
   the conversation; unprepared later actions are reassessed after the approval decision.
   Use an available preparation action or delegate the complete business objective
   with proposal permission. Do not ask for execution permission before preparation.
   An information-only request permits investigation, not preparing a business change.
3. Runtime presents the complete prepared set and obtains one execution approval,
   including policy-required confirmation of targets, full item list, consequences
   and payment terms. Such pre-execution confirmations belong here, not stage 1.
   Ask execution approval for all prepared actions selected for presentation,
   not for other requested changes that are merely discussed or still queued.
A missing-input question must ask only for its missing value/choice; do not add
confirmation of already resolved targets or permission to proceed. Prior assent
without a matching prepared action is user intent, not a runtime approval grant.
This preserves required user choices without collecting execution approval twice.
An action decision applies only to the identified proposal. A declined proposal
must not be prepared again in the unchanged objective; continue other requested
outcomes, or report that the declined action was not performed. A declined action
is no longer an execution obligation. Expiry is not user rejection or goal
cancellation: refresh the proposal if still requested and obtain a new approval.
Only explicit goal cancellation retires the entire objective and its dependants.
Distinguish user-required final outcomes from intermediate tool effects. A tool
leaving a state unchanged does not make that state a final user requirement. Before
declaring requested changes incompatible or asking the user to choose, consider an
ordering that satisfies their prerequisites and preserves the requested final
outcomes. Follow an explicit user ordering; do not silently reorder it. Choose the
first feasible action only when later parameters depend on its result; otherwise
prepare the ready set together. Resolve actual uncertainty before preparation.
"""


def action_presentation_instruction(pending_actions):
    """One presentation rule for the author and its existing final verifier.

    Only proposals selected by the runtime for this reply confer a purpose to
    solicit approval. Historical prose and failed preparation cannot create it.
    This is a semantic instruction, not a grant or a text classifier.
    """
    if pending_actions:
        return ("This reply presents a runtime-prepared action. Ask execution approval only for "
                "the supplied pending_actions and their exact scope, not queued changes. Explain "
                "their terms once; preserve independently completed results. User approval remains required.")
    return ("This reply has NO prepared action selected for approval. Do not ask the user to confirm "
            "execution, say 'confirm so I can proceed', or claim an action is ready for approval. "
            "An earlier assistant confirmation question and the user's yes cannot create a prepared proposal. "
            "If preparation failed, explain that failure and what remains undone; another yes cannot "
            "repair a failed tool or plan. Genuine missing-value/choice questions and explanations of "
            "general policy remain valid. Do not promise that a requested combination is executable "
            "from separate successful reads or tool availability: check the combined constraints. "
            "A complete investigation result is not a prepared action or a completed business change.")


def reply_presentation_instruction(context):
    """Author and verifier read the same runtime presentation facts, once."""
    instruction = action_presentation_instruction(context.get("pending_actions", ()))
    instruction += (" Requested objectives describe what the user wants, not what is ready. "
                    "An outcome's observed_segment describes only the latest worker segment; "
                    "WAITING_APPROVAL does not mean its entire requested objective is prepared. "
                    "Only pending_actions identifies the operations presented for approval.")
    execution = context.get("turn_execution") or {}
    if execution.get("continues_after_reply") is False:
        instruction += (
            " This turn's execution has ended; no further work is scheduled after this reply. "
            "Unfinished goals, retryable failures and saved progress are not ongoing execution. "
            "Describe what completed and what stopped. Ask a needed value or present a selected "
            "approval when supplied, explaining that continuation requires that input. "
            "Do not promise autonomous continuation, a later update or a future confirmation. "
            "A possible next step is not a scheduled action.")
    return instruction


def merge_action_decisions(*groups):
    """A consumed approval has one immutable outcome across checkpoint imports."""
    decisions = {}
    for group in groups:
        for decision in group:
            prior = decisions.setdefault((decision["approval_id"], decision.get("operation_key")), decision)
            if prior != decision:
                raise ConversationStateConflict("approval decision differs across checkpoints")
    return tuple(decisions.values())


def action_decision_context(previous, pending, resolution):
    """Checkpointed evidence of consumed decisions, not a second approval store."""
    from application.deterministic_resolution import ResolutionKind
    decisions = tuple(previous or ())
    if pending is None or resolution.kind not in {
        ResolutionKind.APPROVAL_DECISION, ResolutionKind.APPROVAL_EXPIRED,
    }:
        return decisions
    operations = getattr(pending, "operations", None) or (
        SimpleNamespace(
            operation_key=getattr(pending, "operation_key", None),
            action_ref=pending.action_ref,
            arguments=pending.arguments,
        ),
    )
    decisions_for_scope = []
    for op in operations:
        decision = {
            "approval_id": pending.approval_id,
            "action_ref": op.action_ref,
            "arguments": {arg.name: arg.value for arg in op.arguments},
            "control_id": pending.origin_control.control_id if pending.origin_control else None,
            "decision": ("EXPIRED" if resolution.kind is ResolutionKind.APPROVAL_EXPIRED
                         else "APPROVED" if resolution.approved else "DECLINED"),
        }
        if op.operation_key is not None:
            decision["operation_key"] = op.operation_key
        decisions_for_scope.append(decision)
    return merge_action_decisions(decisions, decisions_for_scope)


def partition_work_revision(suspended, affected_controls):
    """Split a suspended DAG at any explicitly changed goal, not just its root."""
    excluded = {item.work_item_id for item in suspended
                if item.control and item.control.control_id in affected_controls}
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
                if result.status.value in {"SUCCEEDED", "CANCELLED", "SUPERSEDED"}
                or result.assignment_issue is not None}
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
    from application.approval_operation import ApprovalOperation, approval_scope_key
    actions = result.prepared_actions
    for prepared in actions:
        _validate_prepared_action(prepared, parent, registry)
    action = actions[0]
    definition = registry.action(action.action_ref)
    operations = tuple(ApprovalOperation(a.action_ref, a.operation_key, a.aggregate_ref,
        a.target_entity_version, a.arguments, a.argument_bindings) for a in actions)
    scope_key = approval_scope_key(operations)
    stream_id = "action-workstream:" + scope_key
    stream = WorkstreamState(
        stream_id, parent.owner_agent, definition.ref, "PREPARED",
        WorkstreamStatus.WAITING_APPROVAL, 1, action.arguments, definition.flow_ref,
    )
    return state.wait_for_approval(PendingApprovalState(
        action.approval_binding if len(actions) == 1 else "approval:" + scope_key,
        1, stream_id, action.work_item_id, definition.ref,
        action.operation_key, action.aggregate_ref, action.target_entity_version,
        (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        action.arguments, checkpoint_thread_id, action.argument_bindings,
        suspended_work_items=(parent, *(
            work for work in plan.work.items if work.work_item_id != parent.work_item_id
            and work.work_item_id not in finished | waiting
        )),
        origin_work_item_id=parent.work_item_id,
        control=parent.control,
        additional_operations=operations[1:],
    ), new_workstream=stream)


def _validate_prepared_action(action, parent, registry):
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
