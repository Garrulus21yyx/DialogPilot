"""Evaluation capture via framework callbacks, not a second model transport."""
import time
from langchain_core.callbacks import BaseCallbackHandler


class FrameworkCapture(BaseCallbackHandler):
    raise_error = True
    run_inline = True

    def __init__(self, *, limit=20, calls=None):
        self.limit = limit
        self.calls = calls if calls is not None else []
        self._active = {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        if len(self.calls) >= self.limit:
            raise RuntimeError("evaluation_model_call_limit")
        batch, = messages
        row = {"request": {
            **kwargs.get("invocation_params", {}),
            "system": next((m.content for m in batch if m.type == "system"), ""),
            "messages": [{"role": "user" if m.type == "human" else m.type, "content": m.content}
                         for m in batch if m.type != "system"],
        }}
        self.calls.append(row)
        self._active[run_id] = (row, time.perf_counter())

    def on_llm_end(self, response, *, run_id, **kwargs):
        row, start = self._active.pop(run_id)
        message = response.generations[0][0].message
        row.update(raw_output=message.model_dump(mode="json"), usage=message.usage_metadata or {},
                   latency_ms=(time.perf_counter() - start) * 1000)

    def on_llm_error(self, error, *, run_id, **kwargs):
        pending = self._active.pop(run_id, None)
        if pending:
            row, start = pending
            row.update(error_type=type(error).__name__, latency_ms=(time.perf_counter()-start)*1000)
