"""Capacity failures recover the model step, not the business operation."""
import asyncio
import copy
import json

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from infrastructure.target_model_recovery import is_context_overflow, recover_model_request
from infrastructure.target_context_compaction import ContextCompaction
from infrastructure.target_history_summary import summarize_history
from infrastructure.target_planning_compaction import fit_planning_context
from infrastructure.target_result_archive import TargetResultArchive
from tests.test_target_framework_agent import ScriptedToolModel, _context


class Overflow(Exception):
    status_code = 400
    body = {"error": {"code": "context_length_exceeded"}}


@pytest.mark.parametrize("error", [ValueError("prompt too long"), TimeoutError(),
                                   type("TooLarge", (Exception,), {"status_code": 413})("image too large")])
def test_other_errors_are_not_capacity_retries(error):
    assert not is_context_overflow(error)


@pytest.mark.parametrize("shrinks", [False, True])
def test_retry_is_bounded_and_each_actual_request_is_smaller(shrinks):
    async def run():
        seen = []
        async def call(value):
            seen.append(value)
            raise Overflow()
        async def shrink(value, target):
            return target if shrinks else value
        with pytest.raises(ModelContextBudgetExceeded):
            await recover_model_request(1000, invoke=call, shrink=shrink, measure=lambda v: v, available=1000)
        assert len(seen) == (3 if shrinks else 1)
        assert all(b < a for a, b in zip(seen, seen[1:]))
    asyncio.run(run())


class SummaryModel(ScriptedToolModel):
    captured: list = []
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.captured.append(messages)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Selected Blue; do not submit."))])


def test_summary_splits_complete_groups_and_merges_with_finite_calls():
    async def run():
        model = SummaryModel(responses=[])
        messages = []
        for i in range(4):
            messages += [AIMessage(content="", tool_calls=[{"id": str(i), "name": "read", "args": {}}]),
                         ToolMessage(content=f"BODY-{i} " + "x" * 1800, tool_call_id=str(i))]
        result = await summarize_history(model, messages, prompt="Preserve choices.\n{messages}", available_tokens=1000)
        assert result == "Selected Blue; do not submit."
        assert 1 < model.calls <= 8
        for call in model.captured:
            assert count_tokens_approximately(call) <= 1000
            text = str(call[0].content)
            for i in range(4):
                if f"BODY-{i}" in text:
                    assert f"'id': '{i}'" in text
    asyncio.run(run())


def test_planner_compacts_history_not_pending_approval_or_current_answer():
    async def run():
        value = {"message": "Yes, use the gift card.", "pending_approval": {"id": "A", "amount": 7196},
                 "conversation_context": {"recent_messages": [
                     {"role": "user", "seq": i + 1, "content": "Old dialogue " * 100} for i in range(12)]}}
        original = copy.deepcopy(value)
        budget = ContextBudgetManager(context_window_tokens=2500, reserved_output_tokens=0, protocol_reserve_tokens=0)
        model = SummaryModel(responses=[])
        fitted = await fit_planning_context(budget, value, token_counter=budget._estimate,
                                           can_read_sources=False, model=model)
        assert fitted.payload["pending_approval"] == value["pending_approval"]
        assert fitted.payload["message"] == value["message"]
        assert fitted.payload["conversation_context"]["recent_messages"] == value["conversation_context"]["recent_messages"][-2:]
        assert "gift card" not in str(model.captured)
        assert value == original
    asyncio.run(run())


def test_fixed_contract_overflow_does_not_spend_summary_calls_or_drop_approval():
    async def run():
        value = {"message": "Yes", "pending_approval": {"details": "required " * 2000},
                 "conversation_context": {"recent_messages": [
                     {"role": "user", "seq": i, "content": "Earlier conversation"} for i in range(10)]}}
        original = copy.deepcopy(value)
        budget = ContextBudgetManager(context_window_tokens=1000, reserved_output_tokens=0, protocol_reserve_tokens=0)
        model = SummaryModel(responses=[])
        with pytest.raises(ModelContextBudgetExceeded):
            await fit_planning_context(budget, value, token_counter=budget._estimate,
                                       can_read_sources=False, model=model)
        assert model.calls == 0 and value == original
    asyncio.run(run())


@pytest.mark.parametrize("chars", [2949, 3257, 12000, 60000])
def test_archive_default_reads_fitting_original_once_and_bounds_large_envelope(chars):
    async def run():
        archive, context = TargetResultArchive(InMemoryStore()), _context()
        text = "x" * chars
        ref = await archive.save(context, {"content": text})
        page = await archive.read(context, ref, max_tokens=4000)
        assert count_tokens_approximately([ToolMessage(content=json.dumps(page), tool_call_id="read")]) <= 4000
        if chars <= 12000:
            assert page["text"] == text and page["next_offset"] is None
        else:
            assert page["next_offset"] == len(page["text"]) < chars
        assert (await archive.load(context, ref))["content"] == text
    asyncio.run(run())


@pytest.mark.parametrize("parallel_reads", [1, 2, 8])
def test_framework_archive_reads_share_the_current_batch_allowance(parallel_reads):
    from types import SimpleNamespace
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from infrastructure.target_agent_middleware import model_overhead_tokens
    from application.default_capability_registry import build_default_capability_registry
    from tests.test_target_framework_agent import _manager
    async def run():
        model, context = ScriptedToolModel(responses=[]), _context()
        agent = TargetFrameworkAgent(model, _manager([]), review_model=model, review_available_tokens=14200,
            result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"), system_prompt="Read.")
        tools = agent._tools(context)
        reader = next(tool for tool in tools if tool.name == "read_tool_result")
        ref = await agent._archive.save(context, {"content": "x" * 60000})
        runtime = SimpleNamespace(context=context, state={"messages": [AIMessage(content="", tool_calls=[
            {"id": str(i), "name": reader.name, "args": {"reference": ref}} for i in range(parallel_reads)])]})
        page = await reader.coroutine(ref, runtime)
        size = count_tokens_approximately([ToolMessage(content=json.dumps(page), tool_call_id="read")])
        net = agent._context_budget.available_tokens - model_overhead_tokens(agent._system(context), tools)
        assert size * parallel_reads <= net // 2
        assert page["next_offset"] is not None
    asyncio.run(run())


class RecoveringActor(ScriptedToolModel):
    actor_calls: int = 0
    fail_until: int = 2
    summary_calls: int = 0
    sizes: list = []
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if "Summarize old customer-service" in str(messages[0].content):
            self.summary_calls += 1
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Earlier comparison completed; Blue selected."))])
        self.actor_calls += 1
        self.sizes.append(count_tokens_approximately(messages))
        if self.actor_calls == 1:
            output = AIMessage(content="", tool_calls=[{"name": "prepare", "id": "prepared", "args": {}}])
        elif self.actor_calls <= self.fail_until:
            raise Overflow()
        else:
            assert any(isinstance(m, ToolMessage) and "approval-A" in m.content for m in messages)
            output = AIMessage(content="Please approve the prepared action.")
        return ChatResult(generations=[ChatGeneration(message=output)])


@pytest.mark.parametrize("backend", ["memory", "postgres"])
@pytest.mark.parametrize("exhaust", [False, True])
def test_native_graph_recovery_retains_tool_result_and_checkpoints_smaller_window(backend, exhaust, request):
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    async def run(saver):
        calls = []
        def prepare() -> str:
            """Prepare a proposal without submitting it."""
            calls.append("prepare")
            return "approval-A already prepared, not executed"
        model = RecoveringActor(responses=[], fail_until=10 if exhaust else 2)
        pinned = HumanMessage(content="Use Blue, wait for approval", id="goal")
        compact = ContextCompaction(model, TargetResultArchive(InMemoryStore()),
            available_tokens=4000, overhead_tokens=100, pinned_message=pinned)
        from infrastructure.target_agent_middleware import AgentContextMiddleware, ModelInvocationMiddleware
        from langchain.agents.middleware import ModelCallLimitMiddleware
        graph = create_agent(model, [prepare], middleware=[compact,
            AgentContextMiddleware(ContextBudgetManager(context_window_tokens=4000,
                reserved_output_tokens=0, protocol_reserve_tokens=0)),
            ModelCallLimitMiddleware(thread_limit=8), ModelInvocationMiddleware()], checkpointer=saver)
        config = {"configurable": {"thread_id": "recovery"}}
        if exhaust:
            with pytest.raises(ModelContextBudgetExceeded):
                await graph.ainvoke({"messages": [pinned, HumanMessage(content="Old context " * 450)]},
                                   config=config, context=_context())
            snapshot = await graph.aget_state(config)
            assert any(isinstance(m, ToolMessage) and "approval-A" in m.content for m in snapshot.values["messages"])
            assert calls == ["prepare"]
            model.fail_until = 0
            result = await graph.ainvoke(None, config=config, context=_context())
            assert result["messages"][-1].text == "Please approve the prepared action."
            assert calls == ["prepare"]
            return
        result = await graph.ainvoke({"messages": [pinned, HumanMessage(content="Old context " * 450)]},
                                    config=config, context=_context())
        assert calls == ["prepare"]
        assert model.actor_calls == 3 and model.summary_calls == 1
        assert model.sizes[-1] < model.sizes[-2]
        assert result["messages"][-1].text == "Please approve the prepared action."
        checkpoint = await graph.aget_state(config)
        assert checkpoint.values["compaction_records"]
        assert checkpoint.values["messages"] == result["messages"]
    if backend == "memory":
        asyncio.run(run(InMemorySaver()))
    else:
        url = request.getfixturevalue("postgres_database_url")
        async def postgres():
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver)
        asyncio.run(postgres())


class RecoveringPlanner(SummaryModel):
    planning_calls: int = 0
    planning_messages: list = []
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if str(messages[0].content).startswith("Summarize historical dialogue"):
            return super()._generate(messages, stop, run_manager, **kwargs)
        self.planning_calls += 1
        self.planning_messages.append(messages)
        if self.planning_calls == 1:
            raise Overflow()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Ready."))])


def test_real_planner_provider_retries_same_approval_input_with_smaller_history():
    from core.model_policy import ModelProfile, ModelRole
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    async def run():
        model = RecoveringPlanner(responses=[])
        profile = ModelProfile("test", max_context_tokens=16000)
        provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
            model_profile=profile, synthesis_profile=profile)
        value = {"message": "Yes, use the gift card.", "supported_goals": [],
                 "pending_approval": {"approval_id": "approval-A", "amount": 7196},
                 "conversation_context": {"recent_messages": [
                     {"role": "user", "seq": i + 1, "content": "Old dialogue " * 100} for i in range(12)]}}
        original = copy.deepcopy(value)
        await provider.plan(value)
        assert model.planning_calls == 2
        assert count_tokens_approximately(model.planning_messages[1]) < count_tokens_approximately(model.planning_messages[0])
        for messages in model.planning_messages:
            assert "approval-A" in str(messages[-1].content)
            assert "Yes, use the gift card." in str(messages[-1].content)
        assert value == original
    asyncio.run(run())
