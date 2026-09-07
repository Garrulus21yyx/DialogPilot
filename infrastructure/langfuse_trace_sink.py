"""Optional Langfuse v4 sink over its OpenTelemetry-native Python SDK."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from typing import Any


logger = logging.getLogger(__name__)
_VALID_TYPES = {
    "span", "generation", "event", "embedding", "agent", "tool",
    "chain", "retriever", "guardrail", "evaluator",
}


class LangfuseTraceSink:
    def __init__(self):
        from langfuse import Langfuse

        self.public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        self.client = Langfuse(mask=mask_observation)

    @classmethod
    def from_env(cls):
        if os.getenv("LANGFUSE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        if not os.getenv("LANGFUSE_PUBLIC_KEY") or not os.getenv("LANGFUSE_SECRET_KEY"):
            raise RuntimeError("LANGFUSE_ENABLED requires LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
        return cls()

    def callback(self):
        from langfuse.langchain import CallbackHandler
        return CallbackHandler(public_key=self.public_key)

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


def mask_observation(*, data, **kwargs):
    """Project data policy at the SDK export boundary; no custom event collector."""
    from core.tracing import TraceRecorder
    from dataclasses import asdict, is_dataclass
    from pydantic import BaseModel
    if isinstance(data, BaseModel):
        return mask_observation(data=data.model_dump())
    if is_dataclass(data) and not isinstance(data, type):
        return mask_observation(data=asdict(data))
    if isinstance(data, dict):
        if data.get("type") in {"thinking", "reasoning", "redacted_thinking"}:
            return {"type": data["type"], "content": "[REDACTED]"}
        return {key: "[REDACTED]" if TraceRecorder._sensitive_key(str(key))
                or str(key).casefold() in {"thinking", "reasoning", "reasoning_content", "signature"}
                else mask_observation(data=value) for key, value in data.items()}
    if isinstance(data, (list, tuple)):
        return [mask_observation(data=value) for value in data]
    return TraceRecorder._redact_text(data) if isinstance(data, str) else data
