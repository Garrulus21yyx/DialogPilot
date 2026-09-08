"""Attribution follows real SDK boundaries and persisted domain outcomes."""
import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from langgraph.store.memory import InMemoryStore

from application.default_capability_registry import build_default_capability_registry
from core.model_policy import ModelPolicy, ModelRole
from infrastructure.langfuse_trace_sink import LangfuseTraceSink, mask_otel_spans
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_target_framework_agent import ScriptedToolModel, _context, _manager


@pytest.fixture
def tracing():
    exporter = InMemorySpanExporter()
    key = "pk-lf-test-" + uuid4().hex
    client = Langfuse(public_key=key, secret_key="test-only", base_url="http://127.0.0.1:1",
        tracer_provider=TracerProvider(), span_exporter=exporter, mask_otel_spans=mask_otel_spans)
    sink = object.__new__(LangfuseTraceSink)
    sink.client, sink.public_key = client, key
    try:
        yield sink, exporter
    finally:
        client.shutdown()


@pytest.mark.parametrize("invalid", [False, True])
def test_approval_model_observation_and_decision_have_same_identity(tracing, invalid):
    pytest.importorskip("tau2")
    from evaluation.tau3_full_adapter import Tau3TargetAgent
    from tests.framework_structured_stub import models
    from application.work_item import ArgumentValue
    sink, exporter = tracing

    async def run():
        agent = Tau3TargetAgent(SimpleNamespace(get_tools=lambda: [], get_policy=lambda: "policy"),
                               loop=asyncio.get_running_loop())
        agent.conversation_id, agent.turn = "attribution-session", 4
        agent.approval_callbacks = (sink.callback(),)
        agent.approval_model = models({"decision": "invalid" if invalid else "approve"},
                                     name="submit_approval_decision")[ModelRole.INTENT]
        pending = SimpleNamespace(approval_id="approval-4", action_ref="order.cancel:v1",
                                  arguments=(ArgumentValue.create("order_id", "A123"),))
        if invalid:
            with pytest.raises(ValueError, match="schema_invalid"):
                await agent._approval_decision(pending, "Yes, proceed.")
            assert agent.trace[0]["detail"]["exception_chain"][-1]["type"] == "ValidationError"
        else:
            assert await agent._approval_decision(pending, "Yes, proceed.") is True
        assert agent.trace[0]["approval_id"] == "approval-4"
        return agent.trace

    asyncio.run(run())
    sink.client.flush()
    generations = [s for s in exporter.get_finished_spans()
                   if s.attributes.get("langfuse.observation.type") == "generation"]
    assert len(generations) == 1
    attributes = dict(generations[0].attributes)
    assert attributes["session.id"] == "attribution-session"
    assert "approval-4" in str(attributes) and "A123" in str(attributes)
    assert "invalid" in attributes["langfuse.observation.output"] if invalid else "approve" in attributes["langfuse.observation.output"]


@pytest.mark.parametrize("error_type", [ConnectionError, ValueError])
def test_domain_failure_keeps_cause_completed_evidence_and_checkpoint(tracing, error_type):
    sink, exporter = tracing
    class Model(ScriptedToolModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.calls:
                raise error_type("password=private-diagnostic failed after lookup")
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
    calls = []
    model = Model(responses=[AIMessage(content="", tool_calls=[{
        "name": "catalog_search", "args": {"query": "product"}, "id": "lookup-completed"}])])
    agent = TargetFrameworkAgent(model, _manager(calls), review_model=model, review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.",
        callbacks=(sink.callback(),), trace_sink=sink)
    with sink.client.start_as_current_observation(name="attribution-turn"):
        result = asyncio.run(agent(_context()))
    diagnostic = next(row for row in result.execution_feedback if row.get("stage") == "agent_execution")
    detail = diagnostic["detail"]
    assert detail["work_item_id"] == result.work_item_id
    assert detail["invocation_key"] == "invocation-a"
    assert detail["exception_chain"][-1]["type"] == error_type.__name__
    assert any(frame["function"] == "_generate" for frame in detail["exception_chain"][-1]["frames"])
    assert "private-diagnostic" not in json.dumps(diagnostic)
    assert len(calls) == 1 and result.facts[0].source_ref == "lookup-completed"
    serializer = target_checkpoint_serializer()
    compacted = replace(result, working_messages=())
    assert serializer.loads_typed(serializer.dumps_typed(compacted)) == compacted
    sink.client.flush()
    spans = exporter.get_finished_spans()
    failure = next(s for s in spans if s.name == "agent_execution")
    assert failure.attributes["langfuse.observation.level"] == "ERROR"
    assert len({s.context.trace_id for s in spans}) == 1
    assert error_type.__name__ in str(failure.attributes)


def test_real_composition_wires_every_domain_to_sdk_and_diagnostics(postgres_database_url, tracing, monkeypatch):
    from infrastructure.postgres import PostgresPool, PostgresPoolConfig, PostgresMigrationRunner
    from infrastructure.target_runtime_composition import build_target_runtime
    import infrastructure.target_runtime_composition as composition
    sink, _ = tracing
    built = []
    def model_factory(profile, *args, **kwargs):
        model = ScriptedToolModel(responses=[])
        built.append((profile, kwargs, model))
        return model
    monkeypatch.setattr(composition, "framework_model", model_factory)
    monkeypatch.setenv("MODEL_CONTEXT_WINDOW_TOKENS", "20000")
    monkeypatch.setenv("CONTEXT_PROTOCOL_RESERVE_TOKENS", "600")
    policy = ModelPolicy.from_env({})
    policy = replace(policy, profiles={**policy.profiles,
        ModelRole.WORKER: replace(policy.profile(ModelRole.WORKER), model="actor-test"),
        ModelRole.VERIFIER: replace(policy.profile(ModelRole.VERIFIER), model="review-test", max_context_tokens=12000)})
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url))
    pool.open()
    async def run():
        from mcp.tool_manager import Tool
        registry = build_default_capability_registry("tenant-a")
        tool_manager = _manager([])
        async def unused_write(params, context):
            pytest.fail("composition must not execute business operations")
        for action in registry.actions:
            owner = registry.agent(action.owner_agent)
            for tool_id in action.allowed_tool_ids:
                definition = registry.tool(tool_id)
                tool_manager.register(Tool(tool_id, f"Business contract for {tool_id}",
                    unused_write, {"type": "object", "properties": {}},
                    allowed_agents=(owner.execution_principal,),
                    authority=definition.authority, read_only=False))
        runtime = await build_target_runtime(database_url=postgres_database_url, postgres_pool=pool,
            tool_manager=tool_manager, memory=SimpleNamespace(), response_delivery=SimpleNamespace(),
            model_policy=policy, provider_config={}, project_root=Path(__file__).parents[1],
            registry=registry, enable_encoder=False, langfuse_sink=sink)
        try:
            assert runtime.orchestration._domain_workers
            assert runtime.application._turn_runtime._assembler._registry is runtime.registry
            assert {entry['tool_id'] for entry in runtime.application._turn_runtime._assembler._action_semantics} == {
                tool_id for action in registry.actions for tool_id in action.allowed_tool_ids}
            for worker in runtime.orchestration._domain_workers.values():
                assert worker._callbacks and worker._trace_sink is sink
                assert worker._model is next(model for profile, _, model in built if profile.model == "actor-test")
                assert worker._review_model is next(model for profile, _, model in built if profile.model == "review-test")
                assert worker._review_model is not worker._model
                assert worker._review_available_tokens == 12000 - 4096 - 600
            assert sum(profile.model == "review-test" for profile, _, _ in built) == 1
        finally:
            await runtime.checkpoint_owner.__aexit__(None, None, None)
    try:
        asyncio.run(run())
    finally:
        pool.close()


def test_parallel_archive_failures_keep_both_call_causes_and_original_facts():
    from infrastructure.target_result_archive import ResultArchiveError
    class UnavailableStore(InMemoryStore):
        async def aput(self, *args, **kwargs):
            try:
                raise ConnectionError("archive connection lost")
            except ConnectionError as exc:
                raise ResultArchiveError("archive unavailable", retryable=True) from exc
    calls = []
    model = ScriptedToolModel(responses=[AIMessage(content="", tool_calls=[{
        "name": "catalog_search", "args": {"query": str(i)}, "id": f"lookup-{i}"} for i in range(2)])])
    result = asyncio.run(TargetFrameworkAgent(model, _manager(calls), review_model=model, review_available_tokens=14200, result_store=UnavailableStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Assist.")(_context()))
    diagnostic = next(row for row in result.execution_feedback if row.get("stage") == "agent_result_archive")
    errors = diagnostic["detail"]["related_errors"]
    assert {error["call_id"] for error in errors} == {"lookup-0", "lookup-1"}
    assert all(error["exception_chain"][-1]["type"] == "ConnectionError" for error in errors)
    assert len(calls) == len(result.facts) == 2
    assert model.calls == 1


def test_nested_parallel_workers_export_each_model_and_tool_once(tracing):
    from langchain_core.runnables import RunnableLambda
    sink, exporter = tracing
    assert sink.callback() is sink.callback()
    calls = []
    async def worker(_):
        model = ScriptedToolModel(responses=[
            AIMessage(content="", tool_calls=[{
                "name": "catalog_search", "args": {"query": "model"}, "id": "lookup"}]),
            AIMessage(content="Found PX-200."),
        ])
        return await TargetFrameworkAgent(model, _manager(calls), review_model=model, review_available_tokens=14200,
            result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"),
            system_prompt="Assist.", callbacks=(sink.callback(),))(_context())

    async def run():
        return await RunnableLambda(worker).abatch([{}, {}],
            config={"callbacks": [sink.callback()]})
    results = asyncio.run(run())
    sink.client.flush()
    spans = exporter.get_finished_spans()
    assert len(calls) == 2
    assert all(result.status.value == "SUCCEEDED" for result in results)
    generations = [s for s in spans if s.attributes.get("langfuse.observation.type") == "generation"]
    tool_spans = [s for s in spans if s.name == "catalog_search"]
    assert len(generations) == 6  # Two actor calls and one assessment per worker.
    assert sum("proposed_outcome" in str(span.attributes.get("langfuse.observation.input", ""))
               for span in generations) == 2
    assert len(tool_spans) == 2
    ids = {span.context.span_id for span in spans}
    assert all(span.parent and span.parent.span_id in ids for span in generations + tool_spans)
