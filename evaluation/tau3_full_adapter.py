"""Half-duplex protocol adapter for the real Target application and τ³.

An async Target turn waits while the official orchestrator executes each tool.
The bridge never reads tasks, expected actions, or the environment database.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from queue import Queue
from threading import Event
import uuid

from tau2.agent.base_agent import HalfDuplexAgent
from tau2.data_model.message import AssistantMessage, MultiToolMessage, ToolCall, ToolMessage

from application.chat_contracts import ChatCommand, Completed, Accepted, NeedsInput, Failed, Reconciling
from infrastructure.postgres_target_runtime import PostgresConversationStateStore


class ObservedVerifier:
    """Keep evaluation diagnostics without changing the production verdict."""
    def __init__(self, verifier, trace):
        self.verifier, self.trace = verifier, trace

    async def verify(self, *args, **request):
        result = await self.verifier.verify(*args, **request)
        self.trace.append({"verification": asdict(result),
                           "candidate": args[1] if len(args) > 1 else request["answer"]})
        return result


class SimulationStopped(RuntimeError):
    """The bridge is closing; this is not a failed or rolled-back business tool."""


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
        self._stopped = Event()
        self._turn_tasks = set()
        self._draining_result = False

    def configure(self, components, pool):
        self.components = components
        self.states = PostgresConversationStateStore(pool)
        self.pool = pool
        self.conversation_id = "tau3-" + uuid.uuid4().hex

    async def call_tool(self, name, arguments, internal_tool_call_id=None):
        if self._stopped.is_set():
            raise SimulationStopped("simulation stopped before a new tool request")
        call_id = "tau3-call-" + uuid.uuid4().hex
        if internal_tool_call_id:
            self.trace.append({"causal_link": {
                "schema_version": "tau3-business-call-binding-v1",
                "turn": self.turn,
                "internal_tool_call_id": str(internal_tool_call_id),
                "business_call_id": call_id,
                "tool_name": name,
            }})
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
        if self._stopped.is_set():
            raise SimulationStopped("simulation stopped before a new turn")
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

    async def _turn(self, text):
        if self._stopped.is_set():
            return
        task = asyncio.current_task()
        self._turn_tasks.add(task)
        try:
            self.turn += 1
            state = self.states.load("default", "benchmark-visitor", self.conversation_id)
            extra = {}
            if state.pending_interaction is not None:
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
            if isinstance(outcome, (Failed, Reconciling)):
                response_id = (outcome.response_id if isinstance(outcome, Failed)
                               else outcome.public_status.get("response_id"))
                if response_id:
                    with self.pool.transaction() as connection:
                        row = connection.execute(
                            "SELECT payload->>'response' FROM dialogpilot_app.response_deliveries "
                            "WHERE publication_id=%s AND tenant_id=%s AND user_id=%s AND conversation_id=%s",
                            (response_id, "default", "benchmark-visitor", self.conversation_id),
                        ).fetchone()
                    if row and isinstance(row[0], str) and row[0].strip():
                        # Deliver the committed notice; the trace above retains
                        # Failed/Reconciling, never a synthetic business success.
                        self.events.put(AssistantMessage(role="assistant", content=row[0]))
                        return
            if not isinstance(outcome, Completed):
                raise RuntimeError("Target did not publish a completed reply: " + type(outcome).__name__)
            self.events.put(AssistantMessage(role="assistant", content=str(outcome.response["response"])))
        except BaseException as exc:
            self.events.put(exc)
        finally:
            self._turn_tasks.discard(task)

    def stop(self, message=None, state=None):
        self._stopped.set()
        self.events.put(SimulationStopped("simulation stopped"))
        self.loop.call_soon_threadsafe(self._stop_on_loop, message)

    def _stop_on_loop(self, message):
        # Official finalization can hold the last result after executing a tool.
        # Deliver it before closing; no new tool can be dispatched during drain.
        messages = message.tool_messages if isinstance(message, MultiToolMessage) else (
            [message] if isinstance(message, ToolMessage) else [])
        for result in messages:
            if result.id in self.pending:
                self._draining_result = True
                self._resolve_tool(result)
        if not self._draining_result:
            for task in tuple(self._turn_tasks):
                task.cancel()

    async def aclose(self):
        """Wait for actual async cleanup, not just the concurrent future flag."""
        self.stop()
        await asyncio.sleep(0)  # apply the queued thread-safe stop request
        if self._turn_tasks:
            await asyncio.gather(*tuple(self._turn_tasks), return_exceptions=True)
