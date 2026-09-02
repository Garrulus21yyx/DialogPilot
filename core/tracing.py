"""跨异步任务传播的轻量 Trace 上下文与有界 Span 存储。"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
import threading
import time
import uuid
import re
from typing import Any, Deque, Dict, Iterator, List, Optional


_trace_id: ContextVar[str] = ContextVar("dialogpilot_trace_id", default="")
_span_id: ContextVar[str] = ContextVar("dialogpilot_span_id", default="")


def current_trace_id() -> str:
    """返回当前异步上下文的 TraceId；未建立 scope 时为空。"""
    return _trace_id.get()


def current_span_id() -> str:
    """返回当前 SpanId，供子 Span 建立父子关系。"""
    return _span_id.get()


@contextmanager
def trace_scope(trace_id: Optional[str] = None) -> Iterator[str]:
    """建立请求级 Trace 上下文；contextvars 会自动传播到 asyncio Task。"""
    resolved = str(trace_id or uuid.uuid4().hex)
    trace_token = _trace_id.set(resolved)
    span_token = _span_id.set("")
    try:
        yield resolved
    finally:
        _span_id.reset(span_token)
        _trace_id.reset(trace_token)


@dataclass(frozen=True)
class SpanRecord:
    """一次已闭合 Span 的不可变审计投影。"""

    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    kind: str
    status: str
    started_at: float
    duration_ms: float
    attributes: Dict[str, Any] = field(default_factory=dict)
    error_type: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """转换为 API 可序列化结构。"""
        return asdict(self)


class TraceRecorder:
    """进程内有界 Trace Owner；生产环境可在同一接口后替换 OTel exporter。"""

    def __init__(self, max_spans: int = 2000):
        if max_spans < 1:
            raise ValueError("max_spans must be positive")
        self._records: Deque[SpanRecord] = deque(maxlen=int(max_spans))
        self._lock = threading.RLock()

    @contextmanager
    def span(
        self,
        name: str,
        *,
        kind: str = "internal",
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Iterator[str]:
        """记录一个 Span，并在异常路径同样产生 error 终态。"""
        trace_id = current_trace_id() or uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        parent_span_id = current_span_id()
        token = _span_id.set(span_id)
        started_at = time.time()
        started_monotonic = time.monotonic()
        status = "ok"
        error_type = ""
        try:
            yield span_id
        except BaseException as exc:
            status = "error"
            error_type = type(exc).__name__
            raise
        finally:
            record = SpanRecord(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=parent_span_id,
                name=str(name)[:160],
                kind=str(kind)[:40],
                status=status,
                started_at=started_at,
                duration_ms=round((time.monotonic() - started_monotonic) * 1000, 3),
                attributes=self._sanitize_attributes(attributes or {}),
                error_type=error_type,
            )
            with self._lock:
                self._records.append(record)
            _span_id.reset(token)

    def get_trace(self, trace_id: str) -> List[SpanRecord]:
        """按开始时间返回一个请求的全部已闭合 Span。"""
        with self._lock:
            records = [record for record in self._records if record.trace_id == trace_id]
        return sorted(records, key=lambda record: (record.started_at, record.span_id))

    def recent(self, limit: int = 100) -> List[SpanRecord]:
        """返回最近 Span 快照，避免调用方直接持有内部 deque。"""
        with self._lock:
            return list(self._records)[-max(0, int(limit)):]

    @classmethod
    def _sanitize_attributes(cls, attributes: Dict[str, Any]) -> Dict[str, Any]:
        """限制 Trace 属性形状和长度，不记录完整 Prompt、密码或工具结果。"""
        clean: Dict[str, Any] = {}
        for key, value in list(attributes.items())[:32]:
            safe_key = str(key)[:80]
            if cls._sensitive_key(safe_key):
                clean[safe_key] = "[REDACTED]"
                continue
            if isinstance(value, (bool, int, float)) or value is None:
                clean[safe_key] = value
            elif isinstance(value, (list, tuple, set)):
                clean[safe_key] = [
                    cls._redact_text(str(item))[:120] for item in list(value)[:16]
                ]
            else:
                clean[safe_key] = cls._redact_text(str(value))[:240]
        return clean

    @staticmethod
    def _sensitive_key(key: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
        return any(token in normalized for token in (
            "authorization", "password", "passwd", "secret", "apikey",
            "accesstoken", "refreshtoken", "cookie", "setcookie",
            "prompt", "messagecontent", "tooloutput", "rawinput",
        ))

    @staticmethod
    def _redact_text(value: str) -> str:
        text = str(value)
        patterns = (
            r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+",
            r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
            r"(?i)\b(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*[^\s,;]+",
            r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
            r"(?<!\d)(?:\+?\d[\d -]{8,}\d)(?!\d)",
        )
        for pattern in patterns:
            text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
        return text
