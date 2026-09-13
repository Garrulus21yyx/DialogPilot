"""Preparation barrier and exact alternatives, using existing approval/input state.

Registry owns resource effects, workers own actual candidates, checkpoints retain
their results. This boundary selects a grant or an explicit choice, never writes.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import combinations
from graphlib import TopologicalSorter
import hashlib
import json

from application.action_compatibility import ActionCompatibilityError, order_prepared_actions
from application.agent_result import AgentResultStatus, RequestedField
from application.approval_operation import ApprovalOperation, approval_scope_key
from application.conversation_state import (
    ConversationStateConflict, PendingApprovalState, PendingInteractionState,
    WorkstreamState, WorkstreamStatus,
)


def _scope(pairs, registry, thread_id):
    pairs = tuple(pairs)
    # Resource groups may arrive in a different order from task dependencies.
    # Preserve both the selected resource ordering and every cross-group edge.
    dependencies = {action.operation_key: tuple(other.operation_key
        for index, (other, owner) in enumerate(pairs)
        if owner.work_item_id in origin.dependencies
        or index < position and _overlap(_resources(action, registry, prepared=True),
                                         _resources(other, registry, prepared=True)))
        for position, (action, origin) in enumerate(pairs)}
    by_key = {action.operation_key: (action, origin) for action, origin in pairs}
    pairs = tuple(by_key[key] for key in TopologicalSorter(dependencies).static_order())
    operations = []
    for action, origin in pairs:
        operations.append(ApprovalOperation(action.action_ref, action.operation_key,
            action.aggregate_ref, action.target_entity_version, action.arguments,
            action.argument_bindings, origin.work_item_id, origin.control,
            dependencies[action.operation_key]))
    key = approval_scope_key(operations)
    action, parent = pairs[0]
    pending = PendingApprovalState(
        action.approval_binding if len(pairs) == 1 else "approval:" + key,
        1, "action-workstream:" + key, action.work_item_id, action.action_ref,
        action.operation_key, action.aggregate_ref, action.target_entity_version,
        (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
        action.arguments, thread_id, action.argument_bindings,
        suspended_work_items=tuple(dict((origin.work_item_id, origin) for _, origin in pairs).values()),
        origin_work_item_id=parent.work_item_id, control=parent.control,
        additional_operations=tuple(operations[1:]))
    stream = WorkstreamState(pending.workstream_id, parent.owner_agent, action.action_ref,
        "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1, action.arguments,
        registry.action(action.action_ref).flow_ref)
    return pending, stream


def _resources(item, registry, *, prepared=False):
    arguments = {arg.name: arg.value for arg in item.arguments}
    refs = (item.action_ref,) if prepared else item.allowed_actions
    resources = set()
    for ref in refs:
        rule = registry.action(ref).state_transition
        # An unresolved target/effect is conservative, not proof of independence.
        target = arguments.get(rule.target_argument) if rule else None
        resources.add((rule.resource, target if isinstance(target, str) and target else None)
                      if rule else (None, None))
    return resources


def _overlap(left, right):
    return any((a is None or c is None or a == c)
               and (b is None or d is None or b == d) for a, b in left for c, d in right)


def _queued_after(origins, outcomes, board):
    """Task edges wait for business completion, not merely preparation.

    Retain unstarted descendants for the approved origins' continuation. A real
    worker failure is not an unstarted descendant and stays at the barrier.
    """
    available = set(origins) | {work.work_item_id for work, result in outcomes
        if board.coverage_for(work, result)["task_completed"]}
    descendants = set(origins)
    queued = []
    while True:
        added = [work for work, result in outcomes if work.work_item_id not in available
            and work.dependencies and set(work.dependencies) <= available
            and descendants.intersection(work.dependencies)
            and (result is None or result in board.blocked_results)]
        if not added:
            return tuple(queued)
        queued.extend(added)
        available.update(work.work_item_id for work in added)
        descendants.update(work.work_item_id for work in added)


def _retain_queued(pending, outcomes, board):
    queued = _queued_after({op.origin_work_item_id for op in pending.operations}, outcomes, board)
    return replace(pending, suspended_work_items=tuple(dict((work.work_item_id, work)
        for work in (*pending.suspended_work_items, *queued)).values()))


def _alternatives(pairs, registry):
    # Bounded maximal feasible subsets. Unknown semantics produce a choice,
    # never a fabricated incompatibility claim or a partially approved full set.
    if len(pairs) > 10:
        return tuple((pair,) for pair in pairs)
    options = []
    identities = []
    for count in range(len(pairs), 0, -1):
        for subset in combinations(pairs, count):
            keys = frozenset(action.operation_key for action, _ in subset)
            if any(keys <= known for known in identities):
                continue
            try:
                ordered = order_prepared_actions(subset, registry)
            except ActionCompatibilityError:
                continue
            identities.append(keys)
            options.append(ordered)
    return tuple(options)


def _choice_field(options, target, reason, retry):
    question = ("Some related preparations failed. Choose retry for unfinished preparations, or explicitly "
                "choose a prepared subset and leave the other changes undone. " if retry else
                "These changes cannot currently be jointly approved. Choose one prepared alternative; "
                "unselected changes will not be performed. ")
    question += ("Reason: " + reason + ". Explain the supplied alternatives in the user's language; "
                 "selection is not execution approval.")
    values = [str(index) for index in range(1, len(options) + 1)] + (["retry"] if retry else [])
    return RequestedField("prepared_alternative", target,
                          json.dumps({"type": "string", "enum": values}), question)


def aggregate_prepared_actions(state, plan, board, registry, thread_id):
    from application.action_approval import _validate_prepared_action
    if plan.work is None or state.pending_approval is not None:
        # Never replace a presented grant. New candidates remain in its graph's
        # retained outcomes until a later decision releases the approval slot.
        return state
    outcomes = board.outcome_items or tuple((work, next((result for result in board.results
        if result.work_item_id == work.work_item_id), None)) for work in plan.work.items)
    consumed = {op.operation_key for grant in state.accepted_approvals for op in grant.operations}
    consumed.update(op.operation_key for op in state.excluded_preparations)
    pairs = []
    for origin, result in outcomes:
        if not state.accepts_work(origin) or result is None:
            continue
        for action in result.prepared_actions:
            if action.operation_key in consumed:
                continue
            _validate_prepared_action(action, origin, registry)
            if result.status is not AgentResultStatus.PREPARED:
                raise ConversationStateConflict("candidate producer must return PREPARED")
            pairs.append((action, origin))
    if not pairs:
        return state
    if len({action.operation_key for action, _ in pairs}) != len(pairs):
        raise ConversationStateConflict("duplicate prepared operation identity")
    # Connected resource groups are the barrier, not every task in a conversation.
    groups = []
    for pair in pairs:
        resources = _resources(pair[0], registry, prepared=True)
        connected = [group for group in groups if any(_overlap(resources,
            _resources(action, registry, prepared=True)) for action, _ in group)]
        merged = [pair, *(member for group in connected for member in group)]
        groups = [group for group in groups if group not in connected] + [merged]
    ready = []
    choice = None
    for group in groups:
        origins = {origin.work_item_id for _, origin in group}
        queued = {work.work_item_id for work in _queued_after(origins, outcomes, board)}
        resources = set().union(*(_resources(action, registry, prepared=True) for action, _ in group))
        unfinished = [(work, result) for work, result in outcomes
            if work.allowed_actions and work.work_item_id not in origins | queued and state.accepts_work(work)
            and not board.coverage_for(work, result)["task_completed"]
            and (result is None or result.status not in {
                 AgentResultStatus.CANCELLED, AgentResultStatus.SUPERSEDED, AgentResultStatus.PREPARED})
            and _overlap(resources, _resources(work, registry))]
        if any(result is None or result.status in {AgentResultStatus.NEEDS_USER_INPUT,
                   AgentResultStatus.NEEDS_EVIDENCE} for _, result in unfinished):
            continue  # the existing field/evidence wait owns continuation
        try:
            ordered = order_prepared_actions(group, registry)
            reason = None
        except ActionCompatibilityError as exc:
            ordered, reason = (), exc.code
        if not unfinished and reason is None:
            ready.extend(ordered)
            continue
        if choice is not None or state.pending_interaction is not None:
            continue
        options = tuple(_scope(option, registry, thread_id)[0]
                        for option in _alternatives(group, registry))
        if not options:
            raise ConversationStateConflict("prepared candidates have no selectable alternative")
        suspended = tuple(dict((work.work_item_id, work) for work in
            (*[origin for _, origin in group], *[work for work, _ in unfinished])).values())
        # State fingerprint includes consumed decisions and accepted goal revisions.
        # Re-presenting after retry is a new decision, even if one candidate stayed.
        round_key = hashlib.sha256((state.fingerprint + (thread_id or "") +
            "".join(work.fingerprint for work in suspended)).encode()).hexdigest()
        choice = PendingInteractionState("preparation-choice:" + round_key, 1,
            (_choice_field(options, suspended[0].work_item_id, reason or "PREPARATION_INCOMPLETE", bool(unfinished)),),
            (), suspended, thread_id, options)
    if choice is not None:
        if ready:
            # One decision phase: disjoint ready work is included in every
            # alternative instead of occupying the approval slot ahead of it.
            by_key = {action.operation_key: (action, origin) for action, origin in pairs}
            options = tuple(_scope((*ready, *(by_key[op.operation_key] for op in option.operations)),
                                   registry, thread_id)[0] for option in choice.approval_options)
            suspended = tuple(dict((work.work_item_id, work) for work in
                (*choice.suspended_work_items, *[origin for _, origin in ready])).values())
            schema = json.loads(choice.requested_fields[0].value_schema)
            choice = replace(choice, approval_options=options, suspended_work_items=suspended,
                requested_fields=(_choice_field(options, choice.requested_fields[0].target_work_item_id,
                    "PREPARATION_SELECTION_REQUIRED", "retry" in schema["enum"]),))
        options = tuple(_retain_queued(option, outcomes, board) for option in choice.approval_options)
        choice = replace(choice, approval_options=options, suspended_work_items=tuple(dict(
            (work.work_item_id, work) for work in (*choice.suspended_work_items,
                *(work for option in options for work in option.suspended_work_items))).values()))
        state = state.wait_for_interaction(choice)
    elif ready:
        # Disjoint groups need no semantic joint check; each was checked above.
        pending, stream = _scope(tuple(ready), registry, thread_id)
        pending = _retain_queued(pending, outcomes, board)
        state = state.wait_for_approval(pending, new_workstream=stream)
    return state
