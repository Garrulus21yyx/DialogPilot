"""Production-chain evaluation runner for the protocol-neutral ChatApplication."""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Mapping, Protocol

from application.chat_contracts import Accepted, ChatCommand, ChatHandler, ChatOutcome, Completed, StageObservation


@dataclass(frozen=True)
class ChatRuntimeOverrides:
    """Replaceable environment handles; the application factory owns adaptation."""

    model: Any = None
    clock: Callable[[], float] = time.perf_counter
    business_backend: Any = None
    knowledge_index: Any = None
    delivery_adapter: Any = None


class ChatApplicationFactory(Protocol):
    def __call__(self, overrides: ChatRuntimeOverrides) -> ChatHandler: ...


@dataclass(frozen=True)
class ChatRunResult:
    command: ChatCommand
    outcome: ChatOutcome
    stages: tuple[StageObservation, ...]
    owner_state: Mapping[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0

    @property
    def public_response(self) -> Mapping[str, Any]:
        return self.outcome.response if isinstance(self.outcome, Completed) else {}


class ChatApplicationRunner:
    """Calls the same application service as production and records typed evidence."""

    def __init__(
        self,
        application_factory: ChatApplicationFactory,
        *,
        overrides: ChatRuntimeOverrides | None = None,
        state_probes: Mapping[str, Callable[[ChatCommand, ChatOutcome], Any]] | None = None,
        completion_reader: Callable[[Accepted], Awaitable[ChatOutcome]] | None = None,
    ):
        self._application_factory = application_factory
        self._overrides = overrides or ChatRuntimeOverrides()
        self._state_probes = dict(state_probes or {})
        self._completion_reader = completion_reader

    def with_overrides(self, **changes: Any) -> "ChatApplicationRunner":
        return ChatApplicationRunner(
            self._application_factory,
            overrides=replace(self._overrides, **changes),
            state_probes=self._state_probes,
            completion_reader=self._completion_reader,
        )

    async def run(self, command: ChatCommand) -> ChatRunResult:
        application = self._application_factory(self._overrides)
        started = self._overrides.clock()
        outcome = await application.handle(command)
        if isinstance(outcome, Accepted):
            if self._completion_reader is None:
                raise RuntimeError("durable evaluation requires a completion reader")
            outcome = await self._completion_reader(outcome)
        elapsed = max(0.0, self._overrides.clock() - started) * 1000
        owner_state: dict[str, Any] = {}
        for name, probe in self._state_probes.items():
            value = probe(command, outcome)
            owner_state[name] = await value if inspect.isawaitable(value) else value
        return ChatRunResult(
            command=command,
            outcome=outcome,
            stages=tuple(getattr(outcome, "stages", ()) or ()),
            owner_state=owner_state,
            latency_ms=elapsed,
        )
