"""Per-call control and context policies for the shared framework Agent."""
from __future__ import annotations

import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.work_control import WorkControlGuard
from core.token_estimator import TokenEstimator
from application.knowledge_tool_contract import knowledge_artifact


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
    """Shrink only the provider view; checkpoints retain complete tool artifacts."""

    def __init__(self, budget: ContextBudgetManager) -> None:
        self.budget = budget
        self.estimator = TokenEstimator()

    async def awrap_model_call(self, request, handler):
        messages = list(request.messages)
        # Schemas and the system message consume the same input window as history.
        overhead = self.estimator.estimate(str(request.system_message)) + sum(
            self.estimator.estimate(json.dumps({
                "name": tool.name, "description": tool.description,
                "schema": tool.args_schema if isinstance(tool.args_schema, dict)
                else tool.get_input_schema().model_json_schema(),
            }, ensure_ascii=False, default=str))
            for tool in request.tools
        )
        available = self.budget.available_tokens - overhead

        def size():
            return sum(self.estimator.estimate(json.dumps({
                "role": message.type, "content": message.content,
                "tool_calls": message.tool_calls if isinstance(message, AIMessage) else (),
            }, ensure_ascii=False, default=str)) for message in messages)

        for index, message in enumerate(messages):
            if isinstance(message, ToolMessage) and message.artifact is not None:
                if knowledge_artifact(message.artifact):
                    continue
                content = str(message.content)
                if self.estimator.estimate(content) > 800:
                    messages[index] = message.model_copy(update={"content": (
                        content[:1600] + "\n[truncated; complete result retained in tool artifact "
                        + message.tool_call_id + "]"
                    )})
        required = size()
        if required > available:
            raise ModelContextBudgetExceeded(required + overhead, self.budget.available_tokens)
        return await handler(request.override(messages=messages))
