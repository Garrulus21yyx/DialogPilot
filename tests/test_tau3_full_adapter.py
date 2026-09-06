"""Official adapter diagnostics preserve the production protocol unchanged."""
import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
import pytest

pytest.importorskip("tau2")
from evaluation.tau3_full_adapter import ObservedVerifier, ModelDiagnostics


def test_verifier_diagnostics_preserve_positional_request_and_verdict():
    @dataclass
    class Verdict:
        grounded: bool = False
    verdict = Verdict()
    calls = []
    class Verifier:
        async def verify(self, *args, **kwargs):
            calls.append((args, kwargs))
            return verdict
    trace = []
    result = asyncio.run(ObservedVerifier(Verifier(), trace).verify("question", "answer", context="context"))
    assert result is verdict
    assert calls == [(("question", "answer"), {"context": "context"})]
    assert trace == [{"verification": {"grounded": False}, "candidate": "answer"}]


def test_model_diagnostics_record_protocol_not_content():
    trace = []
    message = SimpleNamespace(response_metadata={"stop_reason": "max_tokens"},
        usage_metadata={"output_tokens": 1024}, tool_calls=[], invalid_tool_calls=[],
        content="private working content")
    ModelDiagnostics(trace).on_llm_end(SimpleNamespace(generations=[[SimpleNamespace(message=message)]]))
    assert trace[0]["model_response"]["stop_reason"] == "max_tokens"
    assert "private working content" not in str(trace)
