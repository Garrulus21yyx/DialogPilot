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
from application.execution_progress import advance_progress, observation_key, PROGRESS_FEEDBACK


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


def tool_observation_uses_arguments(result):
    from application.knowledge_tool_contract import knowledge_outcome
    from application.agent_result import AgentResultStatus
    return not (result.get("authority") == "knowledge.active_source"
                and knowledge_outcome(result.get("data"))[0] is AgentResultStatus.SUCCEEDED)


class ProgressState(AgentState):
    observed_results: list[str]
    stagnant_rounds: int
    progress_warning: bool
    progress_blocked: bool
    consumed_progress_calls: list[str]


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
        consumed = set(state.get("consumed_progress_calls", ()))
        calls = {call["id"]: call for message in state["messages"] if isinstance(message, AIMessage)
                 for call in message.tool_calls}
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact or {}
            if artifact.get("schema") == "agent-result-v1":
                # Interactions/actions are governed by their own boundary.
                continue
            if message.tool_call_id in consumed:
                continue
            consumed.add(message.tool_call_id)
            result = artifact.get("result", {})
            digests = artifact.get("observation") or tool_observation_digests(result if result else {
                "data": message.content, "status": message.status})
            # Old checkpoints contain one digest; new knowledge observations
            # carry one per evidence item, so recombining old hits is not novelty.
            if isinstance(digests, str):
                digests = [digests]
            # Successful knowledge novelty is per evidence, not query rephrasing.
            uses_arguments = artifact.get("observation_uses_arguments", tool_observation_uses_arguments(result))
            arguments = calls.get(message.tool_call_id, {}).get("args") if uses_arguments else None
            batch.extend(observation_key(message.name, arguments, digest) for digest in digests)
        if not batch:
            return None
        update = {**advance_progress(state, batch), "consumed_progress_calls": sorted(consumed)}
        if update["progress_blocked"]:
            return {**update, "jump_to": "end"}
        if update["progress_warning"]:
            update["messages"] = [HumanMessage(content=PROGRESS_FEEDBACK)]
        return update


class OutcomeState(AgentState):
    outcome_review_calls: int
    accepted_outcome: dict
    outcome_feedback: list[dict]


class InteractionBoundaryMiddleware(AgentMiddleware):
    """Accept a goal-bound handback before ending or executing an interaction."""

    state_schema = OutcomeState
    max_rejections = 2

    def __init__(self, action_tools=(), *, review):
        self.action_tools = frozenset(action_tools)
        self.review = review

    def proposed_outcome(self, message):
        calls = message.tool_calls if isinstance(message, AIMessage) else ()
        proposals = [call for call in calls if call["name"] in self.action_tools]
        interactions = {"request_user_input": "NEEDS_USER_INPUT", "report_blocked": "BLOCKED"}
        if len(proposals) > 1 or len(calls) > 1 and any(call["name"] in interactions for call in calls):
            return None
        if calls and not proposals and not (len(calls) == 1 and calls[0]["name"] in interactions):
            return None
        kind = "PREPARE_ACTION" if proposals else interactions[calls[0]["name"]] if calls else "COMPLETE"
        candidate = ({"tool": proposals[0]["name"], "arguments": proposals[0]["args"]}
                     if proposals else calls[0]["args"] if calls else message.text)
        return kind, candidate

    def review_budget(self, messages, context):
        outcome = self.proposed_outcome(messages[-1])
        if outcome is None:
            return None
        kind, candidate = outcome
        return lambda history: (self.review.required_tokens(context=context,
            messages=history, kind=kind, candidate=candidate), self.review.available_tokens)

    @staticmethod
    def _prepared(state, context):
        historical_calls = {entry["data"].get("tool_call_id") for entry in context.working_messages
                            if entry.get("type") == "tool"}
        return any(record.get("pending_action") and call_id not in historical_calls
                   for call_id, record in state.get("tool_observations", {}).items())

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        message = state["messages"][-1]
        calls = message.tool_calls if isinstance(message, AIMessage) else ()
        proposals = [call for call in calls if call["name"] in self.action_tools]
        if len(proposals) > 1:
            return {"messages": [ToolMessage(
                content="No calls in this batch were executed. Prepare one action after completing the checks needed for that choice. A successful preparation ends this segment; the complete remaining objective stays pending for continuation. Do not request a separate execution confirmation.",
                tool_call_id=call["id"], name=call["name"], status="error") for call in calls],
                "jump_to": "model"}
        if len(calls) > 1 and any(call["name"] in {"request_user_input", "report_blocked"} for call in calls):
            return {"messages": [ToolMessage(
                content="No tools in this batch were executed. Make one interaction call, or perform evidence calls first and ask afterwards.",
                tool_call_id=call["id"], name=call["name"], status="error") for call in calls],
                "jump_to": "model"}
        outcome = self.proposed_outcome(message)
        if outcome is None:
            return None
        kind, candidate = outcome
        review_calls = state.get("outcome_review_calls", 0)
        # Accepted proposals may encounter a preparation failure. They consume
        # model/tool budget, not semantic correction budget. Persisted feedback
        # is the authority, including checkpoints written before this change.
        rejections = sum(entry.get("accepted") is False
                         for entry in state.get("outcome_feedback", ()))
        if rejections >= self.max_rejections:
            raise DomainOutcomeRejected("domain_outcome_correction_budget_exhausted")
        assessment = await self.review.assess(context=runtime.context,
            messages=state["messages"], kind=kind, candidate=candidate)
        feedback = [*state.get("outcome_feedback", ()), {"kind": kind, **assessment}]
        update = {"outcome_review_calls": review_calls + 1, "outcome_feedback": feedback,
                  "accepted_outcome": {}}
        if assessment["accepted"]:
            return {**update, "accepted_outcome": {"kind": kind, "message_id": message.id,
                "tool_call_id": proposals[0]["id"] if proposals else calls[0]["id"] if calls else None}}
        if rejections + 1 >= self.max_rejections:
            # A typed failure is retained by the adapter; nothing is relabelled
            # complete and no rejected input tool gets a durable observation.
            raise DomainOutcomeRejected(assessment["feedback"])
        correction = "Internal task review (not a user reply or approval): " + assessment["feedback"]
        return {**update, "jump_to": "model", "messages": ([ToolMessage(
            content=correction, tool_call_id=call["id"], name=call["name"], status="error") for call in calls]
            if calls else [HumanMessage(content=correction)])}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        # This hook runs after the complete tool batch and result persistence.
        # The proposal is the handback; customer prose belongs to Publication.
        if self._prepared(state, runtime.context):
            return {"jump_to": "end"}
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
