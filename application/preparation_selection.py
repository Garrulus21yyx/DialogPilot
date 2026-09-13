"""Pure projections of a prepared-alternative selection onto conversation state."""
from dataclasses import replace
import json

CHOICE_FIELD = "prepared_alternative"


def choice_context(pending):
    """One state-owned choice view for interpretation and response composition.

    These are alternatives, not pending approvals. Business arguments are data
    for the author, not pre-rendered customer text or executable authorization.
    """
    if pending is None or not pending.approval_options:
        return None
    field = next(field for field in pending.requested_fields if field.field_name == CHOICE_FIELD)
    return {"status": "AWAITING_SELECTION_NOT_APPROVED", "question": field.question_hint,
            "retry_available": "retry" in json.loads(field.value_schema)["enum"],
            "alternatives": [{"value": str(index), "operations": [{"action": op.action_ref,
                "arguments": {arg.name: arg.value for arg in op.arguments}} for op in option.operations]}
                for index, option in enumerate(pending.approval_options, 1)]}


def split_wait(pending):
    normal_fields = tuple(field for field in pending.requested_fields if field.field_name != CHOICE_FIELD)
    normal_ids = {field.target_work_item_id for field in normal_fields}
    while True:
        expanded = normal_ids | {work.work_item_id for work in pending.suspended_work_items
                                if normal_ids.intersection(work.dependencies)}
        if expanded == normal_ids:
            break
        normal_ids = expanded
    ordinary = tuple(work for work in pending.suspended_work_items if work.work_item_id in normal_ids)
    choice_work = tuple(work for work in pending.suspended_work_items if work.work_item_id not in normal_ids)
    return normal_fields, ordinary, choice_work


def bind_selection(pending, state, values):
    from application.conversation_state import ConversationStateError
    normal_fields, ordinary, choice_work = split_wait(pending)
    selection = next((value for _, name, value in values if name == CHOICE_FIELD), None)
    normal_values = tuple(value for value in values if value[1] != CHOICE_FIELD)
    remaining, waiting, resumed = normal_fields, ordinary, ()
    if normal_values:
        normal = replace(pending, requested_fields=normal_fields, suspended_work_items=ordinary, approval_options=())
        remaining, waiting, resumed = normal.bind_values(state, normal_values)
    if selection is None:
        return (*remaining, *(field for field in pending.requested_fields if field.field_name == CHOICE_FIELD)), (*waiting, *choice_work), resumed
    if selection == "retry":
        prepared = {work.work_item_id for option in pending.approval_options for work in option.suspended_work_items}
        retry = tuple(work for work in choice_work if work.work_item_id not in prepared)
        if not retry:
            raise ConversationStateError("this alternative has no unfinished preparation to retry")
        return remaining, waiting, (*resumed, *retry)
    if selection not in {str(index) for index in range(1, len(pending.approval_options) + 1)}:
        raise ConversationStateError("unknown prepared alternative")
    return remaining, waiting, resumed


def apply_selection(previous, next_state, pending, values):
    from application.conversation_state import ConversationStateConflict, WorkstreamState, WorkstreamStatus, WorkControlStatus
    selection = next((value for _, name, value in values if name == CHOICE_FIELD), None)
    if selection is None or selection == "retry":
        return next_state
    selected = pending.approval_options[int(selection) - 1]
    _, _, choice_work = split_wait(pending)
    if any(not previous.accepts_work(work) for work in choice_work):
        raise ConversationStateConflict("prepared alternative was revised")
    selected_controls = {work.control for work in selected.suspended_work_items}
    selected_keys = {op.operation_key for op in selected.operations}
    excluded = {op.operation_key: op for op in previous.excluded_preparations}
    excluded.update({op.operation_key: op for option in pending.approval_options
                     for op in option.operations if op.operation_key not in selected_keys})
    next_state = replace(next_state, excluded_preparations=tuple(excluded.values()))
    for work in choice_work:
        if work.control and work.control not in selected_controls:
            next_state = next_state.close_work_control(work.control, status=WorkControlStatus.CANCELLED)
    origin = selected.suspended_work_items[0]
    stream = WorkstreamState(selected.workstream_id, origin.owner_agent, selected.action_ref,
        "PREPARED", WorkstreamStatus.WAITING_APPROVAL, 1, selected.arguments)
    return next_state.wait_for_approval(selected, new_workstream=stream)
