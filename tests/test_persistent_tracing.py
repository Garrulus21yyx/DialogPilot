import asyncio
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from core.llm_metrics import create_message
from core.model_policy import ModelProfile, ModelRole
from core.tracing import TraceRecorder, trace_scope
from infrastructure.langfuse_trace_sink import LangfuseTraceSink
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_trace_sink import PostgresTraceSink


@pytest.fixture()
def trace_store(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    pool = PostgresPool(PostgresPoolConfig(postgres_database_url))
    pool.open()
    with pool.transaction() as connection:
        connection.execute("TRUNCATE dialogpilot_app.trace_spans")
    try:
        yield PostgresTraceSink(pool)
    finally:
        pool.close()


def test_sanitized_hierarchy_and_error_survive_process_memory(trace_store):
    recorder = TraceRecorder()
    recorder.configure_sinks([trace_store])
    with trace_scope("a" * 32):
        with recorder.span(
            "application.chat", kind="agent",
            attributes={"user_id": "opaque-user", "api_key": "secret-value"},
        ):
            with pytest.raises(RuntimeError):
                with recorder.span("tool.refund", kind="tool"):
                    raise RuntimeError("provider body must not be stored")

    rows = trace_store.get_trace("a" * 32)
    assert [row["name"] for row in rows] == ["application.chat", "tool.refund"]
    assert rows[1]["parent_span_id"] == rows[0]["span_id"]
    assert rows[1]["status"] == "error"
    assert rows[1]["error_type"] == "RuntimeError"
    assert rows[0]["attributes"]["api_key"] == "[REDACTED]"
    assert "provider body" not in repr(rows)


def test_model_call_emits_generation_with_model_and_official_usage():
    captured = []

    class Sink:
        @contextmanager
        def span(self, handle):
            yield
            captured.append(handle)

        def close(self):
            pass

    class Messages:
        async def create(self, **_request):
            return SimpleNamespace(
                id="provider-request-1", content=[],
                usage=SimpleNamespace(input_tokens=12, output_tokens=7),
            )

    recorder = TraceRecorder()
    recorder.configure_sinks([Sink()])
    with trace_scope("b" * 32):
        with recorder.span("application.chat", kind="agent"):
            asyncio.run(create_message(
                SimpleNamespace(messages=Messages()),
                ModelProfile("deepseek-v4-flash"),
                ModelRole.REACT,
                max_tokens=64,
                messages=[{"role": "user", "content": "opaque"}],
            ))

    generation = next(item for item in captured if item.name == "llm.generate")
    assert generation.kind == "llm"
    assert generation.attributes["model"] == "deepseek-v4-flash"
    assert generation.attributes["input_tokens"] == 12
    assert generation.attributes["output_tokens"] == 7


def test_langfuse_sink_maps_agent_generation_and_flushes(monkeypatch):
    observations = []

    class Observation:
        def __init__(self, kwargs):
            self.kwargs = kwargs
            self.updates = []

        def update(self, **kwargs):
            self.updates.append(kwargs)

    class FakeLangfuse:
        def __init__(self):
            self.shutdown_called = False

        @contextmanager
        def start_as_current_observation(self, **kwargs):
            observation = Observation(kwargs)
            observations.append(observation)
            yield observation

        def shutdown(self):
            self.shutdown_called = True

    client = FakeLangfuse()
    monkeypatch.setitem(
        __import__("sys").modules,
        "langfuse",
        SimpleNamespace(Langfuse=lambda: client),
    )
    recorder = TraceRecorder()
    recorder.configure_sinks([LangfuseTraceSink()])
    with trace_scope("c" * 32):
        with recorder.span("application.chat", kind="internal"):
            with recorder.span(
                "llm.generate", kind="llm",
                attributes={"model": "deepseek-v4-flash"},
            ) as generation:
                generation.set_attributes(input_tokens=5, output_tokens=3)
    recorder.close()

    assert [item.kwargs["as_type"] for item in observations] == [
        "agent", "generation",
    ]
    assert observations[1].updates[-1]["usage_details"] == {
        "input": 5, "output": 3,
    }
    assert client.shutdown_called is True
