"""Entry probes preserve production input/outcome contracts without task leakage."""
import asyncio
from types import SimpleNamespace

import pytest

from application.chat_contracts import Completed, Failed, NeedsInput
from application.default_capability_registry import build_default_capability_registry
from evaluation.chat_application_runner import ChatApplicationRunner
from evaluation.tau3_adapter import Tau3ChatBridge, capability_overlap


@pytest.mark.parametrize("outcome", [
    Completed("p1", {"response": "What is your order number?"}),
    Failed("PROVIDER_FAILURE", True, "trace-1"),
    NeedsInput("run-1", "signal-1", "INPUT", "2030-01-01", "p2"),
])
def test_bridge_preserves_outcome_and_only_supplies_user_text(outcome):
    commands = []

    class App:
        async def handle(self, command):
            commands.append(command)
            return outcome

    bridge = Tau3ChatBridge(ChatApplicationRunner(lambda _: App()),
        tenant_id="eval", user_id="visitor", conversation_id="task-0")

    async def exercise():
        return [await bridge.receive(text) for text in ("Cancel my order", "Order #123")]

    rows = asyncio.run(exercise())
    assert [command.message for command in commands] == ["Cancel my order", "Order #123"]
    assert [command.request_id for command in commands] == ["turn-1", "turn-2"]
    assert {command.user_id for command in commands} == {"visitor"}
    assert all(command.pinned_bundle is None for command in commands)
    assert all(row["outcome_type"] == type(outcome).__name__ for row in rows)
    assert all(row["response"] == ("What is your order number?" if isinstance(outcome, Completed) else None)
               for row in rows)


@pytest.mark.parametrize("text", ["", "  ", None, 123])
def test_invalid_input_does_not_consume_turn(text):
    bridge = Tau3ChatBridge(None, tenant_id="eval", user_id="visitor", conversation_id="c")
    with pytest.raises(ValueError):
        asyncio.run(bridge.receive(text))
    assert bridge.turn == 0


def test_matching_tool_name_does_not_prove_semantic_compatibility():
    report = capability_overlap(build_default_capability_registry("eval"),
        [SimpleNamespace(name="order_lookup"), SimpleNamespace(name="get_user_details")])
    assert report["exact_name_overlap"] == ["order_lookup"]
    assert report["official_reward_eligible"] is False
    assert report["semantic_bindings_verified"] is False
