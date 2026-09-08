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
