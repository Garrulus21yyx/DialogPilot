"""Progress is semantic, replay-safe, and independent of graph stop mechanics."""
import asyncio
import itertools

from langchain_core.messages import AIMessage, ToolMessage

from application.execution_progress import advance_progress, observation_key
from infrastructure.target_agent_middleware import AgentProgressMiddleware
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer


def test_progress_algebra_generated_sequences_and_checkpoint():
    serde = target_checkpoint_serializer()
    for sequence in itertools.product(range(3), repeat=5):
        state, seen, stagnant, warned = {}, set(), 0, False
        for value in sequence:
            before = dict(state)
            assert advance_progress(state, []) == before
            new = value not in seen
            stop = state.get("progress_blocked", False) or (not new and warned)
            stagnant = 0 if new else stagnant + 1
            warned = not new and stagnant >= 2
            seen.add(value)
            state = advance_progress(state, [str(value), str(value)])
            assert state["progress_blocked"] == stop
            assert state["progress_warning"] == warned
            assert state["stagnant_rounds"] == stagnant
            assert state == serde.loads_typed(serde.dumps_typed(state))


def test_request_and_result_identity_ignore_mapping_order_not_values():
    base = observation_key("tool", {"a": 1, "b": 2}, "not found")
    assert base == observation_key("tool", {"b": 2, "a": 1}, "not found")
    assert base != observation_key("tool", {"a": 2, "b": 2}, "not found")
    assert base != observation_key("tool", {"a": 1, "b": 2}, "found")


def test_native_call_pairing_new_arguments_replay_and_mixed_interaction():
    async def run():
        middleware, state = AgentProgressMiddleware(), {}
        for index in range(5):
            call_id = str(index)
            call = AIMessage(content="", tool_calls=[{"id": call_id, "name": "lookup",
                "args": {"email": f"user{index}@example.test"}}])
            result = ToolMessage(content="not found", name="lookup", tool_call_id=call_id,
                artifact={"schema": "tool-result-v1", "result": {"status": "rejected", "error": "not found"}})
            interaction = ToolMessage(content="question", name="ask", tool_call_id="ask",
                artifact={"schema": "agent-result-v1"})
            # Interaction must not hide the independent read in the same batch.
            messages = [call, result, interaction]
            update = await middleware.abefore_model({**state, "messages": messages}, None)
            assert update["stagnant_rounds"] == 0
            state.update(update)
            assert await middleware.abefore_model({**state, "messages": messages}, None) is None
        assert len(state["observed_results"]) == 5
    asyncio.run(run())
