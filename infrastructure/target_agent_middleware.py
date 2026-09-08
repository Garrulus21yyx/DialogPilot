"""Per-call control and context policies for the shared framework Agent."""
from __future__ import annotations

import json
import hashlib

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.work_control import WorkControlGuard
from core.framework_models import invoke_model
from infrastructure.target_domain_outcome import DomainOutcomeRejected


class ModelInvocationMiddleware(AgentMiddleware):
    async def awrap_model_call(self, request, handler):
        return await invoke_model(handler(request), stage="domain_model")


def model_overhead_tokens(system_message, tools):
    system = system_message if isinstance(system_message, SystemMessage) else SystemMessage(content=system_message or "")
    schemas = [{"name": tool.name, "description": tool.description,
                "schema": tool.tool_call_schema if isinstance(tool.tool_call_schema, dict)
                else tool.tool_call_schema.model_json_schema()} for tool in tools]
    return count_tokens_approximately([system, HumanMessage(content=json.dumps(schemas, ensure_ascii=False))])


def tool_observation_digest(result):
    from application.knowledge_tool_contract import knowledge_progress_identity
    observation = {key: result.get(key) for key in ("data", "error", "status", "effect_status")}
    if result.get("authority") == "knowledge.active_source":
        observation["data"] = knowledge_progress_identity(result.get("data"))
    return hashlib.sha256(json.dumps(observation, sort_keys=True, default=str).encode()).hexdigest()


def tool_observation_digests(result):
    from application.knowledge_tool_contract import knowledge_outcome, knowledge_progress_identity
    from application.agent_result import AgentResultStatus
    if (result.get("authority") == "knowledge.active_source"
            and knowledge_outcome(result.get("data"))[0] is AgentResultStatus.SUCCEEDED):
        identity = knowledge_progress_identity(result["data"])
        return [tool_observation_digest({**result, "authority": None,
            "data": {**identity, "items": [item]}}) for item in identity["items"]]
    return [tool_observation_digest(result)]


class ProgressState(AgentState):
    observed_results: list[str]
    stagnant_rounds: int
    progress_warning: bool
    progress_blocked: bool


class AgentProgressMiddleware(AgentMiddleware):
    """Bound repeated observations, not legitimate new tool evidence.

    Native graph state owns the counters. Two rounds without new observations
    offer one model-directed recovery round; continued stagnation ends this
    segment. Fresh user input starts a new segment, including explicit refresh.
    """

    state_schema = ProgressState

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        batch = []
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact or {}
            if artifact.get("schema") == "agent-result-v1":
                # Interactions/actions are governed by their own boundary.
                return None
            result = artifact.get("result", {})
            digests = artifact.get("observation") or tool_observation_digests(result if result else {
                "data": message.content, "status": message.status})
            # Old checkpoints contain one digest; new knowledge observations
            # carry one per evidence item, so recombining old hits is not novelty.
            if isinstance(digests, str):
                digests = [digests]
            batch.extend(hashlib.sha256(json.dumps([message.name, digest]).encode()).hexdigest()
                         for digest in digests)
        if not batch:
            return None
        seen = set(state.get("observed_results", ()))
        new = set(batch) - seen
        stagnant = 0 if new else state.get("stagnant_rounds", 0) + 1
        update = {"observed_results": sorted(seen | set(batch)), "stagnant_rounds": stagnant,
                  "progress_warning": False if new else state.get("progress_warning", False)}
        if not new and state.get("progress_warning", False):
            return {**update, "progress_blocked": True, "jump_to": "end"}
        if stagnant >= 2:
            update.update(progress_warning=True, messages=[HumanMessage(
                content="Execution feedback: the last two tool rounds produced no new evidence or changed outcome. Review the recorded calls and errors, change the approach, ask for missing input, or report the blocker. Do not repeat the unchanged calls. Completed results remain valid; no task state has been reset.")])
        return update


class OutcomeState(AgentState):
    outcome_review_calls: int
    accepted_outcome: dict
    outcome_feedback: list[dict]


class InteractionBoundaryMiddleware(AgentMiddleware):
    """Accept a goal-bound handback before ending or executing an interaction."""

    state_schema = OutcomeState
    max_review_calls = 2

    def __init__(self, action_tools=(), *, review):
        self.action_tools = frozenset(action_tools)
        self.review = review

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        message = state["messages"][-1]
        calls = message.tool_calls if isinstance(message, AIMessage) else ()
        proposals = [call for call in calls if call["name"] in self.action_tools]
        historical_calls = {entry["data"].get("tool_call_id") for entry in runtime.context.working_messages
                            if entry.get("type") == "tool"}
        prepared = any(record.get("pending_action") and call_id not in historical_calls
                       for call_id, record in state.get("tool_observations", {}).items())
        if len(proposals) > 1 or (prepared and any(
                call["name"] in self.action_tools | {"request_user_input", "report_blocked"}
                for call in calls)):
            return {"messages": [ToolMessage(
                content="No calls in this batch were executed. Only one prepared action is supported per segment. Continue read-only checks and answer the user's questions; describe the pending action as not executed. Do not request a separate confirmation or replace the prepared action.",
                tool_call_id=call["id"], name=call["name"], status="error") for call in calls],
                "jump_to": "model"}
        if len(calls) > 1 and any(call["name"] in {"request_user_input", "report_blocked"} for call in calls):
            return {"messages": [ToolMessage(
                content="No tools in this batch were executed. Make one interaction call, or perform evidence calls first and ask afterwards.",
                tool_call_id=call["id"], name=call["name"], status="error") for call in calls],
                "jump_to": "model"}
        if calls and not proposals and not (len(calls) == 1 and calls[0]["name"] in {"request_user_input", "report_blocked"}):
            return None
        if prepared and not calls:
            # Preparation and approval are deterministic business authorities.
            # The conversation's existing answer check owns approval wording;
            # a second semantic gate here would duplicate that decision.
            return None
        kind = ("PREPARE_ACTION" if proposals else
                {"request_user_input": "NEEDS_USER_INPUT", "report_blocked": "BLOCKED"}[calls[0]["name"]]
                if calls else "COMPLETE")
        candidate = ({"tool": proposals[0]["name"], "arguments": proposals[0]["args"]}
                     if proposals else calls[0]["args"] if calls else message.text)
        review_calls = state.get("outcome_review_calls", 0)
        if review_calls >= self.max_review_calls:
            raise DomainOutcomeRejected("domain_outcome_correction_budget_exhausted")
        assessment = await self.review.assess(context=runtime.context,
            messages=state["messages"], kind=kind, candidate=candidate)
        feedback = [*state.get("outcome_feedback", ()), {"kind": kind, **assessment}]
        update = {"outcome_review_calls": review_calls + 1, "outcome_feedback": feedback,
                  "accepted_outcome": {}}
        if assessment["accepted"]:
            return {**update, "accepted_outcome": {"kind": kind, "message_id": message.id,
                "tool_call_id": proposals[0]["id"] if proposals else calls[0]["id"] if calls else None}}
        if review_calls:
            # A typed failure is retained by the adapter; nothing is relabelled
            # complete and no rejected input tool gets a durable observation.
            raise DomainOutcomeRejected(assessment["feedback"])
        correction = "Internal task review (not a user reply or approval): " + assessment["feedback"]
        return {**update, "jump_to": "model", "messages": ([ToolMessage(
            content=correction, tool_call_id=call["id"], name=call["name"], status="error") for call in calls]
            if calls else [HumanMessage(content=correction)])}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact
            if (isinstance(artifact, dict) and artifact.get("schema") == "agent-result-v1"
                    and artifact.get("result", {}).get("producer_version") == "domain-interaction-v1"):
                if state.get("accepted_outcome", {}).get("tool_call_id") != message.tool_call_id:
                    raise DomainOutcomeRejected("domain_interaction_has_no_accepted_outcome")
                return {"jump_to": "end"}
        return None


class WorkControlMiddleware(AgentMiddleware):
    def __init__(self, guard: WorkControlGuard | None) -> None:
        self.guard = guard

    def check(self, context) -> None:
        if self.guard is not None:
            self.guard.ensure_current(context.work_item, context.trusted_context)

    async def awrap_model_call(self, request, handler):
        self.check(request.runtime.context)
        response = await handler(request)
        self.check(request.runtime.context)
        return response

    async def awrap_tool_call(self, request, handler):
        self.check(request.runtime.context)
        response = await handler(request)
        self.check(request.runtime.context)
        return response


class AgentContextMiddleware(AgentMiddleware):
    """Validate the complete provider view against the configured model budget.

    A byte prefix is not a semantic projection of a structured tool result. Keep
    in-budget payloads intact; genuine overflow has an explicit typed outcome.
    """

    def __init__(self, budget: ContextBudgetManager) -> None:
        self.budget = budget

    async def awrap_model_call(self, request, handler):
        messages = list(request.messages)
        # Schemas and the system message consume the same input window as history.
        overhead = model_overhead_tokens(request.system_message, request.tools)
        available = self.budget.available_tokens - overhead

        required = count_tokens_approximately(messages)
        if required > available:
            raise ModelContextBudgetExceeded(required + overhead, self.budget.available_tokens)
        return await handler(request.override(messages=messages))
