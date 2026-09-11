"""Behavioral witnesses: model repeats, but valid reads do not hit the backend."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from application.agent_result import AgentResultStatus
from application.agent_working_state import working_state, recovery_context
from application.default_capability_registry import build_default_capability_registry
from application.work_item import WorkControlBinding, WorkPlan
from infrastructure.target_framework_agent import TargetFrameworkAgent
from mcp.read_reuse import ReadReusePolicy, read_key
from test_target_framework_agent import ScriptedToolModel, _manager, _item, _context


def read(call_id, **extra):
    return AIMessage(content="", tool_calls=[{"name": "catalog_search", "id": call_id,
        "args": {"query": "product", **extra}}])


def agent(model, manager, store, trace_sink=None):
    from infrastructure.conversation_read_reuse import ConversationReadReuse
    manager.read_reuse = ConversationReadReuse(store, build_default_capability_registry("tenant-a"), trace_sink=trace_sink)
    return TargetFrameworkAgent(model, manager, review_model=model,
        review_available_tokens=14200, result_store=store,
        registry=build_default_capability_registry("tenant-a"), system_prompt="Assist with the objective.",
        trace_sink=trace_sink)


class CausalSink:
    def __init__(self):
        self.events = []

    def record_causal_event(self, event_type, **fields):
        self.events.append((event_type, fields))


@pytest.mark.parametrize("split", [1, 2, 3])
def test_read_history_and_reuse_survive_every_continuation_split(split):
    async def run():
        calls, store = [], InMemoryStore()
        manager = _manager(calls)
        manager.registered_tools[0].task_read_reuse = ReadReusePolicy(300)
        original = replace(_item(), control=WorkControlBinding("goal", 1), max_steps=12)
        first = ScriptedToolModel(responses=[*(read(f"read-{i}") for i in range(split)),
            AIMessage(content="", tool_calls=[{"name": "request_user_input", "id": "ask",
                "args": {"question": "Which color?"}}])])
        result = await agent(first, manager, store)(_context(original))
        assert result.status is AgentResultStatus.NEEDS_USER_INPUT
        assert len(calls) == 1
        assert result.working_state["observed_results"]
        saved = await store.asearch(("target-originals",), limit=100)
        observed_at = [entry.value["observed_at"] for entry in saved if entry.namespace[-1] == "results"]
        assert observed_at
        updated = replace(original, work_item_id="next", continuation_of=original.work_item_id,
                          control=WorkControlBinding("goal", 2))
        # Deliberately remove all model history: reuse/progress are not a summary.
        second = ScriptedToolModel(responses=[read("repeat-after-resume"), AIMessage(content="Done")])
        final = await agent(second, manager, store)(replace(_context(updated), current_message="Blue",
            working_state=result.working_state))
        assert len(calls) == 1
        saved = await store.asearch(("target-originals",), limit=100)
        assert [entry.value["observed_at"] for entry in saved if entry.namespace[-1] == "results"] == observed_at
        assert set(result.working_state["observed_results"]) <= set(final.working_state["observed_results"])
        assert final.status in {AgentResultStatus.SUCCEEDED, AgentResultStatus.BLOCKED}
    asyncio.run(run())


def test_repeated_model_read_records_pre_execution_reuse_decision_and_no_second_backend_call():
    async def run():
        calls, store, sink = [], InMemoryStore(), CausalSink()
        manager = _manager(calls)
        manager.registered_tools[0].task_read_reuse = ReadReusePolicy(300)
        model = ScriptedToolModel(responses=[read("read-1"), read("read-2"), AIMessage(content="Done")])
        result = await agent(model, manager, store, sink)(_context(
            replace(_item(), control=WorkControlBinding("goal", 1), max_steps=8)
        ))
        decisions = [fields for event, fields in sink.events if event == "READ_REUSE_DECIDED"]
        emissions = [fields for event, fields in sink.events if event == "READ_INFORMED_TOOL_EMISSION"]
        assert len(calls) == 1
        assert [item["reuse_decision"] for item in decisions] == [
            "NO_PRIOR_RESULT", "REUSE_VALID_RESULT",
        ]
        assert emissions, sink.events
        assert emissions[0]["emitted_business_call_id"] == "read-2"
        assert result.status is AgentResultStatus.SUCCEEDED
    asyncio.run(run())


@pytest.mark.parametrize("change", ["refresh", "expiry", "authority_age", "mutation", "arguments", "version"])
def test_real_invalidation_runs_the_read_again(change):
    async def run():
        calls, store = [], InMemoryStore()
        manager = _manager(calls)
        tool = manager.registered_tools[0]
        tool.task_read_reuse = ReadReusePolicy(300)
        tool.cache_ttl = 600  # Invalidation must not fall through to a second cache.
        context = _context(replace(_item(), control=WorkControlBinding("goal", 1)))
        result = await agent(ScriptedToolModel(responses=[read("first"), AIMessage(content="Done")]), manager, store)(context)
        state = working_state(result.working_state)
        extra = {"_refresh": True} if change == "refresh" else {}
        if change in {"expiry", "authority_age"}:
            for entry in await store.asearch(("target-originals",), limit=100):
                if entry.namespace[-1] != "results":
                    continue
                record = dict(entry.value)
                record["observed_at"] = (datetime.now(timezone.utc)-timedelta(
                    seconds=61 if change == "authority_age" else 301)).isoformat()
                await store.aput(entry.namespace, entry.key, record)
        if change == "mutation":
            namespace = await manager.read_reuse._namespace(context.trusted_context)
            await store.aput(namespace, "epoch", {"id": "write-attempt"})
        if change == "version":
            tool.output_schema_version = "next-version"
        message = read("second", **extra)
        if change == "arguments":
            message.tool_calls[0]["args"]["query"] = "other product"
        final = await agent(ScriptedToolModel(responses=[message, AIMessage(content="Done")]), manager, store)(
            replace(context, working_state=state))
        assert len(calls) == 2
        assert final.status is AgentResultStatus.SUCCEEDED
    asyncio.run(run())


def test_recovery_keeps_failed_attempts_not_stop_latch_and_is_scope_bound():
    from application.agent_result import AgentResult
    original = replace(_item(), control=WorkControlBinding("goal", 1))
    result = AgentResult(original.work_item_id, original.owner_agent, AgentResultStatus.BLOCKED,
        "AGENT_NO_PROGRESS", "v1", working_state={"observed_results": ["read"],
        "stagnant_rounds": 3, "progress_warning": True, "progress_blocked": True})
    updated = replace(original, work_item_id="recovery", control=WorkControlBinding("goal", 2))
    for target, expected in [(updated, True), (replace(updated, owner_agent="other"), False),
            (replace(updated, registry_fingerprint="changed"), False),
            (replace(updated, control=WorkControlBinding("other", 2)), False)]:
        plan = WorkPlan((target,), target.work_item_id)
        restored = recovery_context(plan, target, ((original, result),))
        assert bool(restored) is expected
        if expected:
            state = restored["working_state"]
            assert state["observed_results"] == ["read"]
            assert state["stagnant_rounds"] == 3
            assert "progress_blocked" not in state


def test_policy_age_boundaries_and_identity_are_deterministic():
    now = datetime.now(timezone.utc)
    policy = ReadReusePolicy(60)
    for seconds in range(-2, 63):
        record = {"epoch": [], "observed_at": (now-timedelta(seconds=seconds)).isoformat()}
        assert policy.valid(record, epoch=[], now=now) == (0 <= seconds < 60)
        assert not policy.valid(record, epoch=["changed"], now=now)
    tool = _manager([]).registered_tools[0]
    first = read_key(tool, {"query": "product"}, _context().trusted_context)
    for key in ("tenant_id", "user_id", "conversation_id"):
        assert first != read_key(tool, {"query": "product"}, {**_context().trusted_context, key: "other"})


def test_parent_graph_recovery_can_finish_from_retained_investigation_without_reading():
    from application.orchestration_runtime import OrchestrationRuntime
    async def run():
        calls, store = [], InMemoryStore()
        manager = _manager(calls)
        manager.registered_tools[0].task_read_reuse = ReadReusePolicy(300)
        original = replace(_item(), max_steps=12, control=WorkControlBinding("goal", 1))
        looping = ScriptedToolModel(responses=[read(f"read-{i}") for i in range(4)])
        worker = agent(looping, manager, store)
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={original.owner_agent: worker})
        first = await runtime.execute(WorkPlan((original,), original.work_item_id),
            current_message="Find the product", trusted_context=_context().trusted_context)
        assert first.results[0].reason_code == "AGENT_NO_PROGRESS"
        assert len(calls) == 1
        inputs = []
        class Recover(ScriptedToolModel):
            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                inputs.extend(messages)
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        fixed = Recover(responses=[AIMessage(content="The recorded model is PX-200.")])
        worker = agent(fixed, manager, store)
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={original.owner_agent: worker})
        target = replace(original, work_item_id="recovery", control=WorkControlBinding("goal", 2))
        board = await runtime.execute(WorkPlan((target,), target.work_item_id),
            current_message="Give me the result already found", trusted_context=_context().trusted_context,
            retained_outcomes=((original, first.results[0]),))
        assert board.results[0].status is AgentResultStatus.SUCCEEDED
        assert len(calls) == 1
        assert any("PX-200" in str(message.content) for message in inputs)
        assert board.results[0].working_state["observed_results"] == first.results[0].working_state["observed_results"]
    asyncio.run(run())


def test_postgres_reopen_preserves_reuse_and_accepts_new_input(postgres_database_url):
    from application.orchestration_runtime import OrchestrationRuntime
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    async def run():
        calls = []
        trusted = {**_context().trusted_context, "conversation_id": "pg-continuation-split"}
        original = replace(_item(), max_steps=12, control=WorkControlBinding("pg-goal", 1))
        manager = _manager(calls)
        manager.registered_tools[0].task_read_reuse = ReadReusePolicy(300)
        first_model = ScriptedToolModel(responses=[read("first"), AIMessage(content="", tool_calls=[{
            "name": "request_user_input", "id": "ask", "args": {"question": "Which color?"}}])])
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner as saver:
            worker = agent(first_model, manager, owner.store)
            runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={original.owner_agent: worker}, checkpointer=saver)
            first = await runtime.execute(WorkPlan((original,), original.work_item_id), current_message="Find the product",
                trusted_context=trusted, thread_id="read-continuity-pg")
            assert first.results[0].status is AgentResultStatus.NEEDS_USER_INPUT
        owner = AsyncPostgresCheckpointOwner(postgres_database_url, setup=True)
        async with owner as saver:
            model = ScriptedToolModel(responses=[read("again"), AIMessage(content="Blue selection recorded.")])
            worker = agent(model, manager, owner.store)
            runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={original.owner_agent: worker}, checkpointer=saver)
            target = replace(original, work_item_id="continued", continuation_of=original.work_item_id,
                             control=WorkControlBinding("pg-goal", 2))
            board = await runtime.resume(WorkPlan((target,), target.work_item_id), current_message="Blue",
                trusted_context=trusted, thread_id="read-continuity-pg")
            assert board.results[0].status is AgentResultStatus.SUCCEEDED
            assert board.results[0].working_state["observed_results"] == first.results[0].working_state["observed_results"]
        assert len(calls) == 1
    asyncio.run(run())


def test_new_planner_controls_reuse_reads_without_inheriting_private_progress():
    from application.orchestration_runtime import OrchestrationRuntime
    from application.turn_planning import TurnPlanCompiler
    from types import SimpleNamespace
    async def run():
        calls, store = [], InMemoryStore()
        manager = _manager(calls)
        manager.registered_tools[0].task_read_reuse = ReadReusePolicy(300)
        for turn in range(4):
            # Exercise the real compiler's fresh-control branch, no handcrafted
            # continuation/recovery relation and no retained native messages.
            control = TurnPlanCompiler._control_binding(
                SimpleNamespace(revises_control_id=None, command_id="semantic-1"), None,
                SimpleNamespace(invocation_key=f"turn-{turn}"), 0)
            item = replace(_item(), work_item_id=f"work-{turn}", control=control)
            model = ScriptedToolModel(responses=[read(f"read-{turn}"), AIMessage(content="Done")])
            worker = agent(model, manager, store)
            runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={item.owner_agent: worker})
            result = await runtime.execute(WorkPlan((item,), item.work_item_id), current_message="Continue",
                trusted_context={**_context().trusted_context, "invocation_key": f"turn-{turn}"})
            assert result.results[0].status is AgentResultStatus.SUCCEEDED
        assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("owner", [a.agent_id for a in build_default_capability_registry("tenant-a").agents])
def test_every_domain_keeps_private_native_state_out_of_model_context(owner):
    registry = build_default_capability_registry("tenant-a")
    definition = registry.agent(owner)
    tool_id = next(name for name in definition.allowed_tool_ids if registry.tool(name).effect.value == "READ")
    context = replace(_context(replace(_item(), owner_agent=owner, allowed_tools=(tool_id,),
        requirement_ids=(registry.tool(tool_id).authority,))),
        working_state={"observed_results": ["PRIVATE-READ-ID"], "reusable_reads": {"PRIVATE-CACHE": {}}})
    model = ScriptedToolModel(responses=[])
    worker = agent(model, _manager([]), InMemoryStore())
    prompt = str(worker._build_prompt(context))
    assert "PRIVATE-READ-ID" not in prompt and "PRIVATE-CACHE" not in prompt
    assert context.current_message in prompt
    assert context.work_item.objective in prompt


def test_observation_assignment_preserves_sources_without_leaking_unrelated_work():
    from application.business_observation import assigned_business_observations
    records = tuple({"reference": f"archive:{i}", "observation": {
        "work_item_id": name, "owner_agent": "product_technical",
        "facts": [{"source_ref": f"source:{i}"}], "receipts": []}}
        for i, name in enumerate(["current", "previous", "dependency", "unrelated", None]))
    selected = assigned_business_observations(records,
        work_item_ids=("current", "previous", None), source_refs=("source:2", None))
    assert selected == records[:3]
    assert all(selected[i] is records[i] for i in range(3))
    assert assigned_business_observations(records, work_item_ids=(None,), source_refs=(None,)) == ()
