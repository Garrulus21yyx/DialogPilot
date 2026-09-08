from langgraph.store.memory import InMemoryStore
"""Real SDK exports from the framework; no custom callback implementation."""
import asyncio
import json
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from application.agent_result import AgentResultStatus
from application.default_capability_registry import build_default_capability_registry
from infrastructure.langfuse_trace_sink import LangfuseTraceSink, mask_observation, mask_otel_spans
from infrastructure.target_framework_agent import TargetFrameworkAgent
from tests.test_target_framework_agent import ScriptedToolModel, _context, _manager


@pytest.mark.parametrize("structured", [True, False])
def test_official_handler_exports_model_tool_hierarchy_and_masked_content(structured):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    key = "pk-lf-test-" + uuid4().hex
    client = Langfuse(public_key=key, secret_key="test-only", base_url="http://127.0.0.1:1",
                      tracer_provider=provider, span_exporter=exporter, mask_otel_spans=mask_otel_spans)

    class PlainModel(ScriptedToolModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model_type = ScriptedToolModel if structured else PlainModel
    model = model_type(responses=[
        AIMessage(content="", tool_calls=[{"name": "catalog_search", "id": "read1", "args": {"query": "model"}}]),
        AIMessage(content="Found PX-200. token=private-token", additional_kwargs={"reasoning_content": "hidden-thought"}),
    ])
    calls = []
    try:
        agent = TargetFrameworkAgent(model, _manager(calls), review_model=model, review_available_tokens=14200, result_store=InMemoryStore(), registry=build_default_capability_registry("tenant-a"),
            system_prompt="Use tools. password=private-password", callbacks=(CallbackHandler(public_key=key),))
        result = asyncio.run(agent(_context()))
        client.flush()
        spans = exporter.get_finished_spans()
        assert spans
        generations = [span for span in spans if span.attributes.get("langfuse.observation.type") == "generation"]
        assert len(generations) == 3  # Two actor calls and one goal assessment.
        assert sum("proposed_outcome" in str(span.attributes.get("langfuse.observation.input", ""))
                   for span in generations) == 1
        assert any(span.attributes.get("langfuse.observation.type") == "tool" for span in spans)
        assert any(span.name == "product_technical_agent" and span.attributes.get("langfuse.observation.type") == "agent"
                   for span in spans)
        assert len({span.context.trace_id for span in spans}) == 1
        ids = {span.context.span_id for span in spans}
        assert all(span.parent and span.parent.span_id in ids for span in generations)
        assert all(span.attributes.get("session.id") == "conversation-a" for span in generations)
        serialized = json.dumps([dict(span.attributes) for span in spans], default=str)
        assert "Found PX-200" in serialized
        assert "product-work-1" in serialized and "invocation-a" in serialized
        for secret in ("private-token", "private-password", "hidden-thought"):
            assert secret not in serialized
        assert len(calls) == 1
        assert result.status is AgentResultStatus.SUCCEEDED
    finally:
        client.shutdown()


def test_langfuse_disabled_and_missing_credentials(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    assert LangfuseTraceSink.from_env() is None
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="requires"):
        LangfuseTraceSink.from_env()


def test_conversation_failure_keeps_generation_and_diagnostic_in_same_trace():
    from core.model_policy import ModelProfile
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    from tests.framework_structured_stub import models
    from tests.test_response_assembly import _board, _verified_order_result
    from application.response_assembly import ResponseAssembler
    exporter = InMemorySpanExporter()
    key = 'pk-lf-test-' + uuid4().hex
    client = Langfuse(public_key=key, secret_key='test-only', base_url='http://127.0.0.1:1',
        tracer_provider=TracerProvider(), span_exporter=exporter, mask_otel_spans=mask_otel_spans)
    sink = object.__new__(LangfuseTraceSink)
    sink.client, sink.public_key = client, key
    provider = AnthropicConversationPlanningProvider(models({'wrong': 'candidate'}, name='submit_composed_response'),
        model_profile=ModelProfile('test'), synthesis_profile=ModelProfile('test'), callbacks=(sink.callback(),))
    board, specs = _board(_verified_order_result()), ()
    try:
        with client.start_as_current_observation(name='turn'):
            result = asyncio.run(ResponseAssembler(provider, trace_sink=sink)._assemble_candidate(
                board, current_message='Continue', requested_inputs=specs))
        client.flush()
        spans = exporter.get_finished_spans()
        generations = [s for s in spans if s.attributes.get('langfuse.observation.type') == 'generation']
        failures = [s for s in spans if s.name == 'composition_model']
        assert len(generations) == len(failures) == 1
        assert len({s.context.trace_id for s in spans}) == 1
        assert 'candidate' in str(generations[0].attributes)
        assert failures[0].attributes['langfuse.observation.level'] == 'ERROR'
        assert result.diagnostics[0].detail['exception_chain'][-1]['type'] == 'ConversationProviderOutputError'
    finally:
        client.shutdown()


def test_mask_preserves_visible_values_and_removes_nested_sensitive_fields():
    source = {"content": [{"type": "thinking", "thinking": "hidden"},
                          {"type": "text", "text": "Refund $13.46; email a@example.com"}],
              "arguments": {"password": "secret", "amount": 13.46}}
    masked = mask_observation(data=source)
    assert masked["arguments"] == {"password": "[REDACTED]", "amount": 13.46}
    assert "Refund $13.46" in str(masked)
    assert "a@example.com" not in str(masked) and "hidden" not in str(masked)
    assert source["arguments"]["password"] == "secret"


def test_sdk_mask_failure_drops_export_without_changing_business_input():
    def broken_policy(*, params):
        raise ValueError("policy failure")
    exporter = InMemorySpanExporter()
    client = Langfuse(public_key="pk-lf-test-" + uuid4().hex, secret_key="test-only",
        base_url="http://127.0.0.1:1", tracer_provider=TracerProvider(),
        span_exporter=exporter, mask_otel_spans=broken_policy)
    source = {"item_id": "7706410293", "password": "private value"}
    try:
        with client.start_as_current_observation(name="mask-failure", input=source):
            pass
        client.flush()
        assert not exporter.get_finished_spans()
        assert source == {"item_id": "7706410293", "password": "private value"}
    finally:
        client.shutdown()
