"""Read-observation progress policy; graph runtimes own persistence and stopping."""
import hashlib
import json

PROGRESS_FEEDBACK = (
    "Execution feedback: the last two tool rounds produced no new evidence or changed outcome. "
    "Review the recorded calls and errors, change the approach, ask for missing input, or report "
    "the blocker. Do not repeat the unchanged calls. Completed results remain valid; no task state has been reset."
)


def observation_key(scope, arguments, outcome):
    return hashlib.sha256(json.dumps([scope, arguments, outcome], sort_keys=True,
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def advance_progress(state, batch):
    """An empty/inapplicable batch does not consume a recovery round."""
    if not batch:
        return dict(state)
    seen = set(state.get("observed_results", ()))
    new = set(batch) - seen
    stagnant = 0 if new else state.get("stagnant_rounds", 0) + 1
    return {"observed_results": sorted(seen | set(batch)),
            "stagnant_rounds": stagnant,
            "progress_warning": not new and stagnant >= 2,
            "progress_blocked": bool(state.get("progress_blocked")) or
                                (not new and bool(state.get("progress_warning")))}


def direct_read_observations(board):
    """Only this execution's pinned reads, not retained history or domain traces."""
    from application.capability_registry import CapabilityEffect
    from application.work_item import ControlMode
    from application.knowledge_tool_contract import knowledge_progress_identity
    from application.agent_result import AgentResultStatus
    results = {result.work_item_id: result for result in board.results}
    batch = []
    for item in board.work_items:
        result = results.get(item.work_item_id)
        if (result is None or not item.observe_result or item.control_mode is not ControlMode.DIRECT
                or item.effect is not CapabilityEffect.READ or len(item.allowed_tools) != 1):
            continue
        if result.status not in {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL,
                                 AgentResultStatus.RETRYABLE_FAILURE, AgentResultStatus.TERMINAL_FAILURE}:
            continue
        knowledge = [fact for fact in result.facts if fact.requirement_id == "knowledge.active_source"]
        if knowledge:
            for fact in knowledge:
                identity = knowledge_progress_identity(json.loads(fact.value_json))
                for evidence in identity["items"]:
                    batch.append(observation_key([item.owner_agent, item.allowed_tools[0]], None,
                        {**identity, "items": [evidence]}))
            continue
        outcome = {"status": result.status.value, "reason": result.reason_code,
                   "facts": sorted((fact.requirement_id, fact.value_json,
                                    fact.producer_id, fact.producer_version) for fact in result.facts),
                   "feedback": [{key: row[key] for key in
                       ("stage", "tool", "status", "error", "effect_status") if key in row}
                       for row in result.execution_feedback]}
        batch.append(observation_key([item.owner_agent, item.allowed_tools[0]],
            {arg.name: arg.value for arg in item.arguments}, outcome))
    return batch


def assignment_repairs(board):
    """Unreplaced assignment failures, including work retained across waits."""
    from application.agent_result import AgentResultStatus
    return tuple(result for _, result in board.outcome_items if result and result.assignment_issue
                 and result.status is AgentResultStatus.TERMINAL_FAILURE) if board else ()


def recovery_observations(board):
    """Current actionable worker failures; retained history cannot retrigger recovery.

    Write outcomes belong to reconciliation, not semantic replanning. A terminal
    programming failure is reportable but not an invitation to repeat the worker.
    """
    from application.agent_result import AgentResultStatus
    from application.capability_registry import CapabilityEffect
    from application.work_item import ControlMode
    if board is None:
        return ()
    results = {result.work_item_id: result for result in board.results}
    return tuple((item, results[item.work_item_id]) for item in board.work_items
        if item.work_item_id in results and item.effect is CapabilityEffect.READ
        and item.control_mode in {ControlMode.DIRECT, ControlMode.DELEGATED}
        and (results[item.work_item_id].status is AgentResultStatus.RETRYABLE_FAILURE
             or results[item.work_item_id].status is AgentResultStatus.BLOCKED
             and results[item.work_item_id].reason_code == "AGENT_NO_PROGRESS"))


def requires_observation(plan, board):
    return bool(plan.work is not None and board is not None and (assignment_repairs(board) or recovery_observations(board)
        or plan.observation_work_item_ids and board.complete))


def planning_observations(board):
    batch = direct_read_observations(board)
    items = {item.work_item_id: item for item, _ in board.outcome_items}
    for result in assignment_repairs(board):
        item = items[result.work_item_id]
        # Attempt IDs/rephrased feedback are not progress on an unchanged scope.
        batch.append(observation_key([item.owner_agent, item.objective],
            [item.allowed_tools, item.allowed_actions], 'assignment_repair'))
    for item, result in recovery_observations(board):
        batch.append(observation_key([item.owner_agent, item.objective],
            [{arg.name: arg.value for arg in item.arguments}, item.allowed_tools, item.allowed_actions],
            {"status": result.status.value, "reason": result.reason_code}))
    return batch
