"""Official adapter diagnostics preserve the production protocol unchanged."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import pytest

pytest.importorskip("tau2")
from evaluation.tau3_full_adapter import ObservedVerifier, Tau3TargetAgent


@pytest.mark.parametrize("decision,expected", [("approve", True), ("deny", False), ("unclear", None)])
def test_text_approval_uses_sdk_contract_and_preserves_full_reply(decision, expected):
    from application.chat_contracts import Completed
    from application.work_item import ArgumentValue
    from core.model_policy import ModelRole
    from tests.framework_structured_stub import models
    calls = []
    text = "Yes, proceed with that action. Also, where can I find the instructions?"

    async def handle(command):
        calls.append(command)
        return Completed("reply", {"response": "Here are the next steps."})

    async def run():
        agent = Tau3TargetAgent(SimpleNamespace(get_tools=lambda: [], get_policy=lambda: "policy"),
                               loop=asyncio.get_running_loop())
        agent.states = SimpleNamespace(load=lambda *args: SimpleNamespace(
            pending_interaction=None, pending_approval=SimpleNamespace(
                approval_id="approval", action_ref="order.cancel:v1",
                arguments=(ArgumentValue.create("order_id", "A123"),))))
        agent.conversation_id = "conversation"
        agent.components = SimpleNamespace(coordinator=SimpleNamespace(handle=handle))
        agent.approval_model = models({"decision": decision}, name="submit_approval_decision")[ModelRole.INTENT]
        await agent._turn(text)
        assert agent.events.get_nowait().content == "Here are the next steps."
        assert agent.trace[0] == {"approval_classification": decision, "conversation_id": "conversation",
                                  "turn": 1, "approval_id": "approval"}

    asyncio.run(run())
    assert calls[0].message == text
    assert calls[0].approval_decision is expected
    assert calls[0].approval_id == ("approval" if expected is not None else None)


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
        agent.states = SimpleNamespace(load=lambda *args: SimpleNamespace(
            pending_interaction=None, pending_approval=SimpleNamespace(approval_id="pending")))
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


def test_clarification_reply_is_not_classified_as_approval():
    from unittest.mock import AsyncMock
    from application.chat_contracts import Completed
    calls = []
    async def handle(command):
        calls.append(command)
        return Completed("reply", {"response": "Explanation"})
    async def run():
        agent = Tau3TargetAgent(SimpleNamespace(get_tools=lambda: [], get_policy=lambda: "policy"),
                               loop=asyncio.get_running_loop())
        agent.states = SimpleNamespace(load=lambda *args: SimpleNamespace(
            pending_interaction=SimpleNamespace(interaction_id="question", version=2),
            pending_approval=SimpleNamespace(approval_id="approval")))
        agent.conversation_id = "conversation"
        agent.components = SimpleNamespace(coordinator=SimpleNamespace(handle=handle))
        agent._approval_decision = AsyncMock(return_value=True)
        await agent._turn("Yes, that is what I meant")
        agent._approval_decision.assert_not_called()
    asyncio.run(run())
    assert calls[0].interaction_id == "question"
    assert calls[0].interaction_version == 2
    assert calls[0].approval_decision is None
