"""Progress is semantic, replay-safe, and independent of graph stop mechanics."""
import asyncio
import itertools
import json
from types import SimpleNamespace

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


def test_progress_emits_owner_events_for_first_read_and_allowed_replay():
    class Sink:
        def __init__(self):
            self.events = []

        def record_causal_event(self, event_type, **fields):
            self.events.append((event_type, fields))

    async def run():
        sink = Sink()
        middleware = AgentProgressMiddleware(sink)
        context = SimpleNamespace(work_item=SimpleNamespace(
            work_item_id="work-1", control=None,
        ), trusted_context={"invocation_key": "turn-1"})
        runtime = SimpleNamespace(context=context)
        call = AIMessage(content="", tool_calls=[{
            "id": "read-1", "name": "lookup", "args": {"id": "1"},
        }])
        result = ToolMessage(content="same", name="lookup", tool_call_id="read-1",
            artifact={"schema": "tool-result-v1", "result": {"status": "OK", "data": "same"}})
        first = await middleware.abefore_model({"messages": [call, result]}, runtime)
        replay_call = call.model_copy(update={"tool_calls": [{
            "id": "read-2", "name": "lookup", "args": {"id": "1"},
        }]})
        replay_result = result.model_copy(update={"tool_call_id": "read-2"})
        await middleware.abefore_model({**first, "messages": [replay_call, replay_result]}, runtime)

        assert [event[0] for event in sink.events] == ["READ_OBSERVED", "READ_REPLAYED"]
        assert sink.events[0][1]["read_identity"] == sink.events[1][1]["read_identity"]
        assert sink.events[1][1]["guard_decision"] == "ALLOW_FIRST_REPLAY"

    asyncio.run(run())


def test_progress_joins_consumed_read_to_next_emitted_tool_call():
    class Sink:
        def __init__(self):
            self.events = []

        def record_causal_event(self, event_type, **fields):
            self.events.append((event_type, fields))

    async def run():
        sink = Sink()
        middleware = AgentProgressMiddleware(sink)
        context = SimpleNamespace(work_item=SimpleNamespace(
            work_item_id="work-1", control=None,
        ), trusted_context={"invocation_key": "turn-1"})
        runtime = SimpleNamespace(context=context)
        call = AIMessage(content="", tool_calls=[{
            "id": "observation-read-1", "name": "read_conversation_observation", "args": {},
        }])
        result = ToolMessage(content="historical", name="read_conversation_observation",
            tool_call_id="observation-read-1", artifact={"schema": "tool-result-v1", "result": {
                "status": "success", "data": "historical",
                "causal_source_call_ids": ["source-business-call-1"],
            }})
        before = await middleware.abefore_model({"messages": [call, result]}, runtime)
        emitted = AIMessage(content="", tool_calls=[{
            "id": "emitted-business-call-2", "name": "get_order_details", "args": {"order_id": "O1"},
        }])
        after = await middleware.aafter_model({**before, "messages": [call, result, emitted]}, runtime)

        observed = sink.events[0]
        informed = sink.events[1]
        assert observed[1]["observation_read_call_id"] == "observation-read-1"
        assert json.loads(observed[1]["source_business_call_ids"]) == ["source-business-call-1"]
        assert informed[0] == "READ_INFORMED_TOOL_EMISSION"
        assert informed[1]["emitted_business_call_id"] == "emitted-business-call-2"
        assert informed[1]["observation_read_call_id"] == "observation-read-1"
        assert json.loads(informed[1]["source_business_call_ids"]) == ["source-business-call-1"]
        assert after == {"causal_read_lineage": []}

    asyncio.run(run())
