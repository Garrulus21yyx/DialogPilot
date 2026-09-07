"""Official adapter diagnostics preserve the production protocol unchanged."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import pytest

pytest.importorskip("tau2")
from evaluation.tau3_full_adapter import ObservedVerifier, Tau3TargetAgent


def test_verifier_diagnostics_preserve_positional_request_and_verdict():
    @dataclass
    class Verdict:
        grounded: bool = False
    verdict = Verdict()
    calls = []
    class Verifier:
        async def verify(self, *args, **kwargs):
            calls.append((args, kwargs))
            return verdict
    trace = []
    result = asyncio.run(ObservedVerifier(Verifier(), trace).verify("question", "answer", context="context"))
    assert result is verdict
    assert calls == [(("question", "answer"), {"context": "context"})]
    assert trace == [{"verification": {"grounded": False}, "candidate": "answer"}]


def test_ambiguous_approval_remains_user_input_without_grant():
    from unittest.mock import AsyncMock
    from application.chat_contracts import Completed
    calls = []
    async def handle(command):
        calls.append(command)
        return Completed("reply", {"response": "The operation still requires your confirmation."})
    async def run():
        agent = Tau3TargetAgent(SimpleNamespace(get_tools=lambda: [], get_policy=lambda: "policy"),
                               loop=asyncio.get_running_loop())
        agent.states = SimpleNamespace(load=lambda *args: SimpleNamespace(pending_approval=SimpleNamespace(approval_id="pending")))
        agent.conversation_id = "conversation"
        agent.components = SimpleNamespace(coordinator=SimpleNamespace(handle=handle))
        agent._approval_decision = AsyncMock(return_value=None)
        await agent._turn("Before approving, can you explain the fee?")
        return agent.events.get_nowait()
    event = asyncio.run(run())
    assert event.content == "The operation still requires your confirmation."
    assert calls[0].approval_id is None
    assert calls[0].approval_decision is None
    assert calls[0].message == "Before approving, can you explain the fee?"
