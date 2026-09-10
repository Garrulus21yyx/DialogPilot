"""Sequential working-set simulation: reread pages must reach a semantic handoff."""
import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage, messages_to_dict, messages_from_dict
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.graph.message import add_messages
from langgraph.store.memory import InMemoryStore

from infrastructure.target_context_compaction import ContextCompaction
from infrastructure.target_result_archive import TargetResultArchive
from tests.test_target_framework_agent import ScriptedToolModel, _context


def facts(messages):
    return dict(re.findall(r"FACT:(\w+)=(\d+)", "\n".join(str(m.content) for m in messages)))


class ExtractiveSummary(ScriptedToolModel):
    """Deterministic summary double; uses ONLY its actual input, no hidden facts."""
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        content = "Comparison observations: " + " ".join(
            f"FACT:{key}={value}" for key, value in sorted(facts(messages).items()))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content))])


def test_repeated_checks_below_summary_trigger_do_not_evict_or_call_model():
    async def run():
        context = _context()
        archive = TargetResultArchive(InMemoryStore())
        model = ExtractiveSummary(responses=[])
        pinned = HumanMessage(content="Compare, do not write", id="goal")
        messages = [pinned]
        for i in range(8):
            messages.extend([
                AIMessage(content="", tool_calls=[{"id": f"page-{i}", "name": "read_tool_result", "args": {}}]),
                ToolMessage(content=f"FACT:p{i}={i} " + "x" * 1000, tool_call_id=f"page-{i}"),
            ])
        compact = ContextCompaction(model, archive, available_tokens=12000,
                                    overhead_tokens=6200, pinned_message=pinned)
        before = messages_to_dict(messages)
        assert 12000 * .70 < compact.count(messages) < compact.trigger
        for _ in range(5):
            assert await compact.abefore_model({"messages": messages}, SimpleNamespace(context=context)) is None
            assert messages_to_dict(messages) == before
        assert model.calls == 0
        assert not await archive.store.asearch(archive.namespace(context))
    asyncio.run(run())


@pytest.mark.parametrize("batch_size", [1, 2, 4])
@pytest.mark.parametrize("page_chars", [1000, 2000, 4000])
@pytest.mark.parametrize("resume", [False, True])
def test_paging_comparison_retains_observations_without_rereading(batch_size, page_chars, resume):
    async def run():
        context = _context()
        archive = TargetResultArchive(InMemoryStore())
        pinned = HumanMessage(content="Compare the candidates. No writes authorized.", id="goal")
        messages = [pinned, HumanMessage(content="Older unrelated dialogue. " * 130)]
        model = ExtractiveSummary(responses=[])
        compact = ContextCompaction(model, archive, available_tokens=12000,
                                    overhead_tokens=6200, pinned_message=pinned, max_summary_calls=20)
        records, expected = [], {}
        # Two interleaved pages per candidate. Every page is fetched exactly once.
        originals = {}
        for product in range(8):
            pages = [f"FACT:p{product}_{page}={product * 17 + page} " + "x" * page_chars
                     for page in range(2)]
            originals[product] = await archive.save(context, {"content": "".join(pages)})
        read_requests = []
        for page in range(2):
            for start in range(0, 8, batch_size):
                calls, results = [], []
                for product in range(start, start + batch_size):
                    value = product * 17 + page
                    marker = f"FACT:p{product}_{page}={value} "
                    offset = 0 if page == 0 else len(f"FACT:p{product}_0={product * 17} ") + page_chars
                    # Archive has an intentional 4000-character bound. Large page
                    # fixture uses its marker plus padding truncated to that bound.
                    length = min(4000, len(marker) + page_chars)
                    args = {"reference": originals[product], "offset": offset, "limit": length}
                    read_requests.append((product, page))
                    call_id = f"read-{product}-{page}"
                    calls.append({"name": "read_tool_result", "id": call_id, "args": args})
                    result = await archive.read(context, **args)
                    results.append(ToolMessage(content=json.dumps(result), tool_call_id=call_id))
                    expected[f"p{product}_{page}"] = str(value)
                messages = add_messages(messages, [AIMessage(content="", tool_calls=calls), *results])
                update = await compact.abefore_model(
                    {"messages": messages, "compaction_records": records}, SimpleNamespace(context=context))
                if update:
                    records.extend(update["compaction_records"])
                    messages = add_messages(messages, update["messages"])
                # This is the actor's actual next input, not the archive contents.
                assert facts(messages) == expected
                assert compact.count(messages) <= compact.available
                tool_ids = {call["id"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls}
                assert all(m.tool_call_id in tool_ids for m in messages if isinstance(m, ToolMessage))
                if resume:
                    messages = messages_from_dict(messages_to_dict(messages))
                    compact = ContextCompaction(model, archive, available_tokens=12000,
                        overhead_tokens=6200, pinned_message=pinned, max_summary_calls=20)
        assert len(read_requests) == len(set(read_requests)) == 16
        assert max(facts(messages), key=lambda k: int(facts(messages)[k])) == "p7_1"
        assert any(r["summary_applied"] for r in records)
    asyncio.run(run())
