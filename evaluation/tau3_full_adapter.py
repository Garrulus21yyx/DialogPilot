"""Half-duplex protocol adapter for the real Target application and τ³.

An async Target turn waits while the official orchestrator executes each tool.
The bridge never reads tasks, expected actions, or the environment database.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from queue import Queue
import uuid
from langchain_core.callbacks import BaseCallbackHandler
from litellm.integrations.custom_logger import CustomLogger

from tau2.agent.base_agent import HalfDuplexAgent
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage

from application.chat_contracts import ChatCommand, Completed, Accepted, NeedsInput
from infrastructure.postgres_target_runtime import PostgresConversationStateStore


class ModelDiagnostics(BaseCallbackHandler):
    """Record protocol outcomes, not prompts, credentials or hidden reasoning."""
    def __init__(self, trace):
        self.trace = trace

    def on_llm_end(self, response, **kwargs):
        for group in response.generations:
            for generation in group:
                message = generation.message
                self.trace.append({"model_response": {
                    "stop_reason": message.response_metadata.get("stop_reason"),
                    "usage": message.usage_metadata,
                    "tool_names": [call["name"] for call in message.tool_calls],
                    "invalid_tool_count": len(message.invalid_tool_calls),
                    "has_content": bool(message.content),
                }})


class UserModelDiagnostics(CustomLogger):
    def __init__(self, trace):
        self.trace = trace

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        choice = response_obj.choices[0]
        self.trace.append({"user_model_response": {
            "finish_reason": choice.finish_reason,
            "has_content": bool(choice.message.content),
            "tool_count": len(choice.message.tool_calls or []),
            "usage": response_obj.usage.model_dump() if response_obj.usage else None,
        }})


class ObservedVerifier:
    """Keep evaluation diagnostics without changing the production verdict."""
    def __init__(self, verifier, trace):
        self.verifier, self.trace = verifier, trace

    async def verify(self, *args, **request):
        result = await self.verifier.verify(*args, **request)
        self.trace.append({"verification": asdict(result),
                           "candidate": args[1] if len(args) > 1 else request["answer"]})
        return result


class Tau3TargetAgent(HalfDuplexAgent):
    def __init__(self, environment, *, loop, timeout_seconds=180):
        super().__init__(environment.get_tools(), environment.get_policy())
        self.loop = loop
        self.timeout_seconds = timeout_seconds
        self.events = Queue()
        self.pending = {}
        self.turn = 0
        self.future = None
        self.trace = []
        self.components = None

    def configure(self, components, pool, client, model):
        self.components = components
        self.states = PostgresConversationStateStore(pool)
        self.pool = pool
        self.client = client
        self.model_profile = model
        self.conversation_id = "tau3-" + uuid.uuid4().hex

    async def call_tool(self, name, arguments):
        call_id = "tau3-call-" + uuid.uuid4().hex
        result = self.loop.create_future()
        self.pending[call_id] = result
        self.events.put(AssistantMessage(role="assistant", tool_calls=[
            ToolCall(id=call_id, name=name, arguments=arguments)]))
        try:
            return await result
        finally:
            self.pending.pop(call_id, None)

    def get_init_state(self, message_history=None):
        if any(not isinstance(message, AssistantMessage) or message.tool_calls
               for message in (message_history or ())):
            raise ValueError("history initialization is outside this development adapter")
        return None

    def generate_next_message(self, message, state):
        if isinstance(message, (ToolMessage, MultiToolMessage)):
            messages = message.tool_messages if isinstance(message, MultiToolMessage) else [message]
            for result in messages:
                self.loop.call_soon_threadsafe(self._resolve_tool, result)
        else:
            self.future = asyncio.run_coroutine_threadsafe(self._turn(message.content), self.loop)
        event = self.events.get(timeout=self.timeout_seconds)
        if isinstance(event, BaseException):
            raise event
        return event, state

    def _resolve_tool(self, message):
        future = self.pending.get(message.id)
        if future is not None and not future.done():
            future.set_result(message)

    async def _approval_decision(self, pending, text):
        # The benchmark is text-only; production UI supplies explicit typed
        # decisions. Classify assent against the exact displayed proposal, not
        # against task answers. Ambiguous/corrective input never grants approval.
        response = await self.client.messages.create(**self.model_profile.request(
            max_tokens=200, temperature=0,
            system="Classify this reply to the exact pending action. Return one JSON object with decision: approve, deny, or unclear. Approve only explicit assent to unchanged parameters. Any correction, extra condition, or ambiguity is unclear.",
            messages=[{"role": "user", "content": json.dumps({
                "action": pending.action_ref,
                "arguments": {arg.name: arg.value for arg in pending.arguments},
                "reply": text}, ensure_ascii=False)}],
        ))
        content = "".join(getattr(block, "text", "") for block in response.content)
        try:
            value = json.loads(content)["decision"]
        except (ValueError, KeyError, TypeError):
            value = "unclear"
        self.trace.append({"approval_classification": value, "usage": response.usage.model_dump()})
        return {"approve": True, "deny": False}.get(value)

    async def _turn(self, text):
        try:
            self.turn += 1
            state = self.states.load("default", "benchmark-visitor", self.conversation_id)
            extra = {}
            if state.pending_approval is not None:
                decision = await self._approval_decision(state.pending_approval, text)
                # Questions and corrections are ordinary new user messages, not
                # approval grants and not a transport failure. The application
                # retains the pending decision while interpreting the new text.
                if decision is not None:
                    extra.update(approval_id=state.pending_approval.approval_id, approval_decision=decision)
            elif state.pending_interaction is not None:
                extra.update(interaction_id=state.pending_interaction.interaction_id,
                             interaction_version=state.pending_interaction.version)
            command = ChatCommand(text, "benchmark-visitor", conv_id=self.conversation_id,
                                  request_id=f"turn-{self.turn}",
                                  authorization_fingerprint="isolated-tau3-environment", **extra)
            outcome = await self.components.coordinator.handle(command)
            if isinstance(outcome, Accepted):
                await self.components.coordinator.pump_once()
                outcome = await self.components.coordinator.await_outcome(outcome, timeout_seconds=180)
            self.trace.append({"turn": self.turn, "input": text, "outcome": asdict(outcome)})
            if isinstance(outcome, NeedsInput):
                with self.pool.transaction() as connection:
                    row = connection.execute(
                        "SELECT payload->>'challenge' FROM dialogpilot_app.response_deliveries "
                        "WHERE publication_id=%s AND tenant_id=%s AND user_id=%s AND conversation_id=%s",
                        (outcome.interaction_publication_id, "default", "benchmark-visitor", self.conversation_id),
                    ).fetchone()
                if row is None:
                    raise RuntimeError("pending interaction has no committed publication")
                self.events.put(AssistantMessage(role="assistant", content=row[0]))
                return
            if not isinstance(outcome, Completed):
                raise RuntimeError("Target did not publish a completed reply: " + type(outcome).__name__)
            self.events.put(AssistantMessage(role="assistant", content=str(outcome.response["response"])))
        except BaseException as exc:
            self.events.put(exc)

    def stop(self, message=None, state=None):
        if self.future is not None and not self.future.done():
            self.future.cancel()
