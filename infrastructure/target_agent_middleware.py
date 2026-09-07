"""Per-call control and context policies for the shared framework Agent."""
from __future__ import annotations

import json
import hashlib

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.work_control import WorkControlGuard
from core.token_estimator import TokenEstimator


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
            observation = {"tool": message.name, "status": result.get("status", message.status),
                "data": result.get("data", message.content), "error": result.get("error"),
                "effect_status": result.get("effect_status")}
            batch.append(hashlib.sha256(json.dumps(observation, sort_keys=True,
                ensure_ascii=False, default=str).encode()).hexdigest())
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


class InteractionBoundaryMiddleware(AgentMiddleware):
    """A bound interaction ends this segment before another model call."""

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state, runtime):
        message = state["messages"][-1]
        calls = message.tool_calls if isinstance(message, AIMessage) else ()
        if len(calls) > 1 and any(call["name"] in {"request_user_input", "report_blocked"} for call in calls):
            return {"messages": [ToolMessage(
                content="No tools in this batch were executed. Make one interaction call, or perform evidence calls first and ask afterwards.",
                tool_call_id=call["id"], name=call["name"], status="error") for call in calls],
                "jump_to": "model"}
        return None

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact
            if (isinstance(artifact, dict) and artifact.get("schema") == "agent-result-v1"
                    and (artifact.get("result", {}).get("status") in {"NEEDS_USER_INPUT", "WAITING_APPROVAL"}
                         or artifact.get("result", {}).get("producer_version") in {"action-preparation-v1", "domain-interaction-v1"})):
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
        self.estimator = TokenEstimator()

    async def awrap_model_call(self, request, handler):
        messages = list(request.messages)
        # Schemas and the system message consume the same input window as history.
        overhead = self.estimator.estimate(str(request.system_message)) + sum(
            self.estimator.estimate(json.dumps({
                "name": tool.name, "description": tool.description,
                "schema": tool.tool_call_schema if isinstance(tool.tool_call_schema, dict)
                else tool.tool_call_schema.model_json_schema(),
            }, ensure_ascii=False, default=str))
            for tool in request.tools
        )
        available = self.budget.available_tokens - overhead

        def size():
            return sum(self.estimator.estimate(json.dumps({
                "role": message.type, "content": message.content,
                "tool_calls": message.tool_calls if isinstance(message, AIMessage) else (),
            }, ensure_ascii=False, default=str)) for message in messages)

        required = size()
        if required > available:
            raise ModelContextBudgetExceeded(required + overhead, self.budget.available_tokens)
        return await handler(request.override(messages=messages))
