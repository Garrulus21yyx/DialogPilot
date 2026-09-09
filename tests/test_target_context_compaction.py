import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict
from langgraph.store.memory import InMemoryStore
from infrastructure.target_context_compaction import ContextCompaction, ToolResultPersistence
from infrastructure.target_result_archive import TargetResultArchive, ResultArchiveError
from tests.test_target_framework_agent import _context, _item, ScriptedToolModel, _manager


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "conversation_id"])
def test_original_is_content_addressed_paged_and_scoped(field):
    async def run():
        archive = TargetResultArchive(InMemoryStore())
        context = _context()
        original = {"content": "原文" * 5000, "artifact": {"schema": "test"}}
        ref = await archive.save(context, original)
        assert await archive.save(context, original) == ref
        chunks, offset = [], 0
        while offset is not None:
            page = await archive.read(context, ref, offset, 700)
            chunks.append(page["text"])
            offset = page["next_offset"]
        assert "".join(chunks) == original["content"]
        for other in (replace(context, trusted_context={**context.trusted_context, field: "other"}),
                      replace(context, work_item=replace(context.work_item, work_item_id="other"))):
            with pytest.raises(ResultArchiveError):
                await archive.load(other, ref)
    asyncio.run(run())


def history(batch_size):
    pinned = HumanMessage(content="Only inspect. Do not submit.", id="current-goal")
    old = HumanMessage(content="Old context. " * 1000, id="old")
    calls = [{"id": f"call-{i}", "name": "lookup", "args": {"id": i}} for i in range(batch_size)]
    latest = [AIMessage(content="", tool_calls=calls, id="latest-model"),
              *(ToolMessage(content=json.dumps({"id": i, "amount": -1346}), tool_call_id=call["id"],
                            id=f"result-{i}") for i, call in enumerate(calls))]
    return pinned, [pinned, old, *latest], latest


@pytest.mark.parametrize("batch_size", [1, 3, 7])
@pytest.mark.parametrize("trailing_note", [False, True])
@pytest.mark.parametrize("sectioned", [False, True])
def test_sdk_summary_preserves_entire_latest_batch_and_pinned_goal(batch_size, trailing_note, sectioned):
    async def run():
        pinned, messages, latest = history(batch_size)
        if sectioned:
            from infrastructure.target_model_context import delegated_task_content
            pinned = pinned.model_copy(update={"content": delegated_task_content({
                "objective": "Only inspect. Do not submit.", "arguments": {}, "requirements": [],
                "action_proposals_allowed": False, "source_conversation": {"current_message": "Inspect the order"},
                "recent_relevant_turns": [], "verified_facts": [],
            })})
            messages[0] = pinned
        if trailing_note:
            messages[1] = messages[1].model_copy(update={"content": "Old context. " * 800})
            messages.append(HumanMessage(content="Additional user constraint: inspect only. " * 80, id="correction"))
        original = messages_to_dict(messages)
        archive = TargetResultArchive(InMemoryStore())
        model = ScriptedToolModel(responses=[AIMessage(content="Old checks completed; no writes authorized.")])
        middleware = ContextCompaction(model, archive, available_tokens=4200,
            overhead_tokens=100, pinned_message=pinned, soft_fraction=.5, summary_fraction=.65)
        update = await middleware.abefore_model({"messages": messages}, SimpleNamespace(context=_context()))
        assert update["compaction_records"][0]["summarized"]
        kept = update["messages"]
        start = next(i for i, message in enumerate(kept) if message.id == "latest-model")
        assert kept[start:start + len(latest)] == latest
        assert pinned in update["messages"]
        assert messages_to_dict(messages) == original
        saved = await archive.load(_context(), update["compaction_records"][0]["original_ref"])
        assert saved["messages"] == original
        assert model.calls == 1
    asyncio.run(run())


def test_result_index_remains_complete_when_sdk_clears_old_messages():
    async def run():
        context = _context()
        archive = TargetResultArchive(InMemoryStore())
        persistence = ToolResultPersistence(archive)
        pinned = HumanMessage(content="Inspect only", id="current")
        messages, records = [pinned], {}
        originals = {}
        for index in range(8):
            call_id = f"lookup-{index}"
            artifact = {"schema": "tool-result-v1", "result": {"data": {
                "record": index, "details": "bounded original " * 80}}}
            raw = ToolMessage(content=json.dumps(artifact["result"]), tool_call_id=call_id, artifact=artifact)
            async def handler(_request):
                return raw
            update = (await persistence.awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=context)), handler)).update
            messages.extend([AIMessage(content="", tool_calls=[{"id": call_id, "name": "lookup", "args": {}}]), *update["messages"]])
            records.update(update["tool_observations"])
            originals[call_id] = artifact
        compact = ContextCompaction(ScriptedToolModel(responses=[AIMessage(content="Historical checks retained by reference.")]),
            archive, available_tokens=6000, overhead_tokens=100, pinned_message=pinned,
            soft_fraction=.4, summary_fraction=.8)
        update = await compact.abefore_model({"messages": messages, "tool_observations": records},
                                            SimpleNamespace(context=context))
        assert update["compaction_records"][0]["after_tokens"] < update["compaction_records"][0]["before_tokens"]
        assert "tool_observations" not in update
        for call_id, record in records.items():
            assert (await archive.load(context, record["reference"]))["artifact"] == originals[call_id]
    asyncio.run(run())


def test_summary_failure_never_replaces_original_history():
    class Unavailable(ScriptedToolModel):
        def _generate(self, *args, **kwargs):
            raise TimeoutError("summary unavailable")
    async def run():
        pinned, messages, _ = history(4)
        original = messages_to_dict(messages)
        archive = TargetResultArchive(InMemoryStore())
        middleware = ContextCompaction(Unavailable(responses=[]), archive,
            available_tokens=4200, overhead_tokens=100, pinned_message=pinned,
            soft_fraction=.5, summary_fraction=.65)
        from core.framework_models import ModelInvocationError
        with pytest.raises(ModelInvocationError, match="context_summary:TimeoutError"):
            await middleware.abefore_model({"messages": messages}, SimpleNamespace(context=_context()))
        assert messages_to_dict(messages) == original
        assert len(await archive.store.asearch(archive.namespace(_context()))) == 1
    asyncio.run(run())


@pytest.mark.parametrize("exhausted", [False, True])
@pytest.mark.parametrize("missing_pin", [False, True])
def test_summary_trigger_and_unhelpful_summary_never_reject_fitting_input(exhausted, missing_pin, monkeypatch):
    async def run():
        pinned, messages, _ = history(3)
        expected = list(messages)
        if missing_pin:
            messages = messages[1:]
        compact = ContextCompaction(ScriptedToolModel(responses=[]),
            TargetResultArchive(InMemoryStore()), available_tokens=6000,
            overhead_tokens=100, pinned_message=pinned, soft_fraction=.3, summary_fraction=.4,
            max_summary_calls=1)
        calls = []
        async def unhelpful(state, runtime):
            calls.append(1)
            return {"messages": list(state["messages"])}
        monkeypatch.setattr(compact.summary, "abefore_model", unhelpful)
        records = [{"summarized": True}] if exhausted else []
        for _ in range(3):
            update = await compact.abefore_model({"messages": messages, "compaction_records": records},
                                                 SimpleNamespace(context=_context()))
            records.extend(update["compaction_records"])
            assert not records[-1]["summary_applied"]
            assert update["messages"][1:] == expected
            assert records[-1]["after_tokens"] <= compact.available
        assert len(calls) == (0 if exhausted else 1)
    asyncio.run(run())


def test_exhausted_summary_budget_still_rejects_actual_overflow():
    from application.context_budget import ModelContextBudgetExceeded
    async def run():
        pinned, messages, _ = history(3)
        compact = ContextCompaction(ScriptedToolModel(responses=[]),
            TargetResultArchive(InMemoryStore()), available_tokens=2000,
            overhead_tokens=100, pinned_message=pinned, max_summary_calls=1)
        with pytest.raises(ModelContextBudgetExceeded):
            await compact.abefore_model({"messages": messages, "compaction_records": [{"summarized": True}]},
                                        SimpleNamespace(context=_context()))
    asyncio.run(run())


@pytest.mark.parametrize("batch_size", [1, 3, 7])
@pytest.mark.parametrize("overhead", [1600, 2000])
def test_history_over_agent_budget_can_use_bounded_summary_invocation(batch_size, overhead):
    async def run():
        pinned, messages, latest = history(batch_size)
        original = messages_to_dict(messages)
        archive = TargetResultArchive(InMemoryStore())
        model = ScriptedToolModel(responses=[AIMessage(content="Old checks done. No writes authorized.")])
        compact = ContextCompaction(model, archive, available_tokens=4200,
            overhead_tokens=overhead, pinned_message=pinned)
        assert compact.count(messages) > compact.available
        update = await compact.abefore_model({"messages": messages}, SimpleNamespace(context=_context()))
        assert model.calls == 1
        assert update["compaction_records"][0]["after_tokens"] <= compact.available
        start = next(i for i, message in enumerate(update["messages"]) if message.id == "latest-model")
        assert update["messages"][start:start + len(latest)] == latest
        assert pinned in update["messages"]
        assert messages_to_dict(messages) == original
    asyncio.run(run())


@pytest.mark.parametrize("oversized", ["protected", "summary"])
def test_each_actual_invocation_rejects_oversized_input_without_model_call(oversized):
    from application.context_budget import ModelContextBudgetExceeded
    async def run():
        pinned, messages, _ = history(1)
        index = -1 if oversized == "protected" else 1
        messages[index] = messages[index].model_copy(update={"content": "x" * 40000})
        original = messages_to_dict(messages)
        model = ScriptedToolModel(responses=[])
        compact = ContextCompaction(model, TargetResultArchive(InMemoryStore()), available_tokens=4200,
            overhead_tokens=100, pinned_message=pinned)
        with pytest.raises(ModelContextBudgetExceeded):
            await compact.abefore_model({"messages": messages}, SimpleNamespace(context=_context()))
        assert model.calls == 0
        assert messages_to_dict(messages) == original
    asyncio.run(run())


def test_archive_failure_checkpoints_original_and_stops_without_model():
    class Unavailable(InMemoryStore):
        async def aput(self, *args, **kwargs):
            raise OSError("storage unavailable")
    async def run():
        middleware = ToolResultPersistence(TargetResultArchive(Unavailable()))
        message = ToolMessage(content="complete original" * 200, tool_call_id="call",
            artifact={"schema": "tool-result-v1", "result": {"success": True, "data": "original"}})
        async def handler(_request):
            return message
        result = await middleware.awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=_context())), handler)
        assert result.update["messages"] == [message]
        assert result.update["archive_failed"]
        assert await middleware.abefore_model(result.update, None) == {"jump_to": "end"}
    asyncio.run(run())


def test_parallel_archive_failures_preserve_both_results_and_stop_segment():
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    class Unavailable(InMemoryStore):
        async def aput(self, *args, **kwargs):
            raise OSError("storage unavailable")
    calls = []
    model = ScriptedToolModel(responses=[AIMessage(content="", tool_calls=[
        {"name": "catalog_search", "id": f"lookup-{i}", "args": {"query": str(i)}}
        for i in range(2)])])
    agent = TargetFrameworkAgent(model, _manager(calls), review_model=model, review_available_tokens=14200, result_store=Unavailable(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Inspect only.")
    result = asyncio.run(agent(_context()))
    assert result.reason_code == "RESULT_ARCHIVE_UNAVAILABLE"
    assert model.calls == 1 and len(calls) == 2
    persisted = {entry["data"]["tool_call_id"] for entry in result.working_messages if entry["type"] == "tool"}
    assert persisted == {"lookup-0", "lookup-1"}
    assert {fact.source_ref for fact in result.facts} == persisted


def test_long_result_can_be_read_without_reexecuting_tool_and_fact_is_complete(monkeypatch):
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from mcp.tool_manager import Tool
    from application.agent_result import AgentResultStatus
    import infrastructure.target_domain_outcome as outcome
    captured = []
    original_review = outcome.structured_call
    async def capture_review(*args, **kwargs):
        captured.append(json.loads(kwargs["messages"][0].content))
        return await original_review(*args, **kwargs)
    monkeypatch.setattr(outcome, "structured_call", capture_review)
    calls, visible = [], []
    manager = _manager(calls)
    original = {"canonical_model": "PX-200", "details": "large detail " * 6000, "amount_minor": -1346}
    async def lookup(_params, _ctx):
        calls.append("lookup")
        return original
    manager.register(Tool(name="catalog_search", description="Catalog", handler=lookup,
        schema={"type": "object", "properties": {}}, authority="product.canonical_model"))
    class Model(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            visible[:] = messages
            self.calls += 1
            if self.calls == 1:
                result = AIMessage(content="", tool_calls=[{"id": "lookup", "name": "catalog_search", "args": {}}])
            elif self.calls == 2:
                pointer = json.loads(messages[-1].content)
                assert pointer["complete"] is False
                result = AIMessage(content="", tool_calls=[{"id": "page", "name": "read_tool_result",
                    "args": {"reference": pointer["result_ref"], "offset": 0, "limit": 1000}}])
            else:
                result = AIMessage(content="Catalog lookup completed.")
            from langchain_core.outputs import ChatResult, ChatGeneration
            return ChatResult(generations=[ChatGeneration(message=result)])
    agent = TargetFrameworkAgent(Model(responses=[]), manager, review_model=Model(responses=[]), review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Inspect catalog.")
    result = asyncio.run(agent(_context()))
    assert result.status is AgentResultStatus.SUCCEEDED
    assert calls == ["lookup"]
    assert json.loads(result.facts[0].value_json) == original
    assert all(len(str(message.content)) < 10000 for message in visible)
    review, = captured
    page = next(m for m in review["working_context"] if m.get("tool_call_id") == "page")
    actor_page = next(m for m in visible if isinstance(m, ToolMessage) and m.tool_call_id == "page")
    assert page["content_json"] == json.loads(actor_page.content)
    assert "large detail" in str(page["content_json"])
    page_call = next(call for m in review["working_context"] for call in m.get("tool_calls", ()) if call["id"] == "page")
    assert page_call["args"]["reference"]


def test_postgres_original_survives_store_reopen(postgres_database_url):
    from langgraph.store.postgres.aio import AsyncPostgresStore
    async def run():
        async with AsyncPostgresStore.from_conn_string(postgres_database_url) as store:
            await store.setup()
            reference = await TargetResultArchive(store).save(_context(), {"content": "original", "artifact": {}})
        async with AsyncPostgresStore.from_conn_string(postgres_database_url) as store:
            assert (await TargetResultArchive(store).read(_context(), reference))["text"] == "original"
    asyncio.run(run())


@pytest.mark.parametrize('batch_size', [1, 2, 5])
def test_admission_preserves_fitting_results_and_offloads_only_under_batch_pressure(batch_size):
    async def run():
        context = _context()
        archive = TargetResultArchive(InMemoryStore())
        persistence = ToolResultPersistence(archive)
        pinned = HumanMessage(content='Read the evidence; keep its conditions.', id='goal')
        calls = [{'id': f'c{i}', 'name': 'lookup', 'args': {}} for i in range(batch_size)]
        messages = [pinned, AIMessage(content='', tool_calls=calls)]
        originals = {}
        for call in calls:
            raw = ToolMessage(content='source condition ' + 'x' * 14000,
                tool_call_id=call['id'], artifact={'schema': 'tool-result-v1', 'result': {'success': True}})
            async def handler(_request):
                return raw
            command = await persistence.awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=context)), handler)
            message = command.update['messages'][0]
            assert message.content == raw.content  # persistence never discards fresh text
            originals[call['id']] = raw.content
            messages.append(message)
        before = messages_to_dict(messages)
        model = ScriptedToolModel(responses=[])
        compact = ContextCompaction(model, archive, available_tokens=14200,
            overhead_tokens=100, pinned_message=pinned)
        fits = compact.count(messages) <= compact.available
        update = await compact.abefore_model({'messages': messages}, SimpleNamespace(context=context))
        final = [m for m in update['messages'] if isinstance(m, (HumanMessage, AIMessage, ToolMessage))] if update else messages
        assert compact.count(final) <= compact.available
        results = [m for m in final if isinstance(m, ToolMessage)]
        assert len(results) == batch_size
        for message in results:
            original = originals[message.tool_call_id]
            stored = await archive.load(context, message.artifact['reference'])
            assert stored['content'] == original
            if fits:
                assert message.content == original
            elif message.content != original:
                pointer = json.loads(message.content)
                assert pointer['complete'] is False
                assert pointer['result_ref'] == message.artifact['reference']
        assert messages_to_dict(messages) == before
        assert model.calls == 0
    asyncio.run(run())
