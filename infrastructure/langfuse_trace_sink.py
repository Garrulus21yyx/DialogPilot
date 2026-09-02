"""Optional Langfuse v4 sink over its OpenTelemetry-native Python SDK."""
from __future__ import annotations

from contextlib import contextmanager
import logging
from typing import Any


logger = logging.getLogger(__name__)
_VALID_TYPES = {
    "span", "generation", "event", "embedding", "agent", "tool",
    "chain", "retriever", "guardrail", "evaluator",
}


class LangfuseTraceSink:
    def __init__(self):
        from langfuse import Langfuse

        self.client = Langfuse()

    @contextmanager
    def span(self, handle):
        observation_type = _observation_type(handle.name, handle.kind)
        metadata = {
            "dialogpilot.trace_id": handle.trace_id,
            "dialogpilot.span_id": handle.span_id,
            **_string_metadata(handle.attributes),
        }
        kwargs: dict[str, Any] = {
            "as_type": observation_type,
            "name": handle.name,
            "metadata": metadata,
        }
        model = str(handle.attributes.get("model") or "").strip()
        if observation_type == "generation" and model:
            kwargs["model"] = model
        with self.client.start_as_current_observation(**kwargs) as observation:
            try:
                yield
            finally:
                update: dict[str, Any] = {
                    "metadata": {
                        **metadata,
                        **_string_metadata(handle.attributes),
                        "duration_ms": str(handle.duration_ms),
                    },
                }
                if handle.status == "error":
                    update.update(
                        level="ERROR", status_message=handle.error_type or "error",
                    )
                if observation_type == "generation":
                    usage = {
                        "input": int(handle.attributes.get("input_tokens") or 0),
                        "output": int(handle.attributes.get("output_tokens") or 0),
                    }
                    update["usage_details"] = usage
                observation.update(**update)

    def close(self) -> None:
        self.client.shutdown()


def _observation_type(name: str, kind: str) -> str:
    normalized = str(kind or "").casefold()
    if normalized == "llm":
        return "generation"
    if normalized in _VALID_TYPES:
        return normalized
    if str(name).startswith(("application.chat", "agent.")):
        return "agent"
    return "span"


def _string_metadata(attributes: dict[str, Any]) -> dict[str, str]:
    return {
        str(key)[:80]: str(value)[:200]
        for key, value in list(attributes.items())[:32]
    }
