"""Per-call control and context policies for the shared framework Agent."""
from __future__ import annotations

import json

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.work_control import WorkControlGuard
from core.token_estimator import TokenEstimator


class InteractionBoundaryMiddleware(AgentMiddleware):
    """A bound interaction ends this segment before another model call."""

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state, runtime):
        for message in reversed(state["messages"]):
            if not isinstance(message, ToolMessage):
                break
            artifact = message.artifact
            if (isinstance(artifact, dict) and artifact.get("schema") == "agent-result-v1"
                    and (artifact.get("result", {}).get("status") in {"NEEDS_USER_INPUT", "WAITING_APPROVAL"}
                         or artifact.get("result", {}).get("producer_version") == "action-preparation-v1")):
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
