"""Official adapter diagnostics preserve the production protocol unchanged."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import pytest

pytest.importorskip("tau2")
from evaluation.tau3_full_adapter import ObservedVerifier, Tau3TargetAgent


@pytest.mark.parametrize("text", ["Yes, proceed. Also explain shipping.", "No, use the other account.", "Explain the fee before I decide."])
def test_text_approval_is_interpreted_by_the_application_not_the_transport(text):
    from application.chat_contracts import Completed
    from application.work_item import ArgumentValue
    calls = []

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
        await agent._turn(text)
        assert agent.events.get_nowait().content == "Here are the next steps."
        assert not hasattr(agent, "_approval_decision")

    asyncio.run(run())
    assert calls[0].message == text
    assert calls[0].approval_decision is None
    assert calls[0].approval_id is None


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
        await agent._turn("Before approving, can you explain the fee?")
        return agent.events.get_nowait()
    event = asyncio.run(run())
    assert event.content == "The operation still requires your confirmation."
    assert calls[0].approval_id is None
    assert calls[0].approval_decision is None
    assert calls[0].message == "Before approving, can you explain the fee?"


def test_clarification_reply_is_not_classified_as_approval():
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
        await agent._turn("Yes, that is what I meant")
    asyncio.run(run())
    assert calls[0].interaction_id == "question"
    assert calls[0].interaction_version == 2
    assert calls[0].approval_decision is None


@pytest.mark.parametrize("kind", ["failed", "reconciling"])
@pytest.mark.parametrize("published", [False, True])
def test_noncompleted_outcome_delivers_only_committed_text_and_retains_status(kind, published):
    from contextlib import contextmanager
    from application.chat_contracts import Failed, Reconciling
    outcome = (Failed("provider_failure", True, "turn", "uncommitted text", response_id="publication")
               if kind == "failed" else Reconciling("run", {"response_id": "publication"}, 1.0))
    queries = []
    class Pool:
        @contextmanager
        def transaction(self):
            yield self
        def execute(self, sql, params):
            queries.append(params)
            return SimpleNamespace(fetchone=lambda: ("Committed notice",) if published else None)
    async def handle(command):
        return outcome
    async def run():
        agent = Tau3TargetAgent(SimpleNamespace(get_tools=lambda: [], get_policy=lambda: "policy"),
            loop=asyncio.get_running_loop())
        agent.states = SimpleNamespace(load=lambda *a: SimpleNamespace(pending_interaction=None))
        agent.conversation_id = "conversation"
        agent.pool = Pool()
        agent.components = SimpleNamespace(coordinator=SimpleNamespace(handle=handle))
        await agent._turn("What happened?")
        event = agent.events.get_nowait()
        if published:
            assert event.content == "Committed notice"
        else:
            assert isinstance(event, RuntimeError)
        assert agent.trace[0]["outcome"] == __import__("dataclasses").asdict(outcome)
    asyncio.run(run())
    assert queries == [("publication", "default", "benchmark-visitor", "conversation")]
