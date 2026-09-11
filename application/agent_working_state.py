"""Private framework continuation state, separate from lossy model history."""
from copy import deepcopy

PROGRESS_FIELDS = ("observed_results", "stagnant_rounds", "progress_warning",
                   "consumed_progress_calls")


def working_state(state):
    # Stop latches, budgets, accepted proposals and publication decisions belong
    # to the old execution. Known observations and failed attempts do not.
    return deepcopy({key: state[key] for key in PROGRESS_FIELDS if key in state})


def recovery_context(plan, item, previous):
    """Only an explicitly revised failed goal inherits its owner's private state."""
    from application.agent_result import AgentResultStatus
    from application.capability_registry import CapabilityEffect
    if item not in plan.items or item.control is None or item.continuation_of:
        return {}
    sources = [(old, result) for old, result in previous
        if result is not None and old.control is not None
        and old.control.control_id == item.control.control_id
        and old.control.revision + 1 == item.control.revision
        and old.owner_agent == item.owner_agent
        and old.registry_fingerprint == item.registry_fingerprint
        and old.effect is CapabilityEffect.READ
        and result.owner_agent == old.owner_agent and result.work_item_id == old.work_item_id
        and (result.status is AgentResultStatus.RETRYABLE_FAILURE
             or result.status is AgentResultStatus.BLOCKED and result.reason_code == "AGENT_NO_PROGRESS"
             or result.status is AgentResultStatus.TERMINAL_FAILURE and result.assignment_issue)]
    if len(sources) > 1:
        raise ValueError("ambiguous recovery working state")
    return {"working_state": working_state(sources[0][1].working_state),
            "working_messages": tuple(sources[0][1].working_messages),
            "facts": tuple(sources[0][1].facts)} if sources else {}
