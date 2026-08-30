"""统一记录模型调用的延迟与供应商 usage。

计量上下文由 API/评测边界显式开启；没有活动 collector 时只执行调用，
不引入全局累计状态。DeepSeek Anthropic 兼容响应目前不单列 reasoning
tokens，因此只记录官方返回的 input/output/cache token 和 thinking block 数。
"""
from __future__ import annotations

import contextvars
import math
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, List, Optional

from core.model_policy import ModelProfile, ModelRole


@dataclass(frozen=True)
class LLMCallUsage:
    """一次 Messages API 调用的不可变计量记录。"""

    role: str
    model: str
    reasoning: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    thinking_blocks: int = 0
    error: Optional[str] = None


def _percentile(values: List[float], percentile: float) -> float:
    """按线性插值计算小样本也有定义的百分位数。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class LLMUsageCollector:
    """当前请求/评测运行的模型调用记录 Owner。"""

    def __init__(self) -> None:
        self._calls: List[LLMCallUsage] = []

    def add(self, call: LLMCallUsage) -> None:
        self._calls.append(call)

    @property
    def calls(self) -> tuple[LLMCallUsage, ...]:
        return tuple(self._calls)

    def summary(self) -> Dict[str, Any]:
        """输出无密钥、可序列化的总计和 role/profile 分组。"""
        groups: Dict[tuple[str, str, str], List[LLMCallUsage]] = {}
        for call in self._calls:
            groups.setdefault((call.role, call.model, call.reasoning), []).append(call)

        def aggregate(items: List[LLMCallUsage]) -> Dict[str, Any]:
            latencies = [item.latency_ms for item in items]
            return {
                "calls": len(items),
                "errors": sum(item.error is not None for item in items),
                "input_tokens": sum(item.input_tokens for item in items),
                "output_tokens": sum(item.output_tokens for item in items),
                "cache_creation_input_tokens": sum(
                    item.cache_creation_input_tokens for item in items
                ),
                "cache_read_input_tokens": sum(item.cache_read_input_tokens for item in items),
                "thinking_calls": sum(item.thinking_blocks > 0 for item in items),
                "latency_ms": {
                    "p50": round(_percentile(latencies, 0.50), 3),
                    "p95": round(_percentile(latencies, 0.95), 3),
                    "max": round(max(latencies), 3) if latencies else 0.0,
                    "sum": round(sum(latencies), 3),
                },
            }

        total = aggregate(self._calls)
        total["reasoning_tokens"] = None
        total["reasoning_tokens_note"] = (
            "DeepSeek Anthropic usage does not expose reasoning tokens separately; "
            "output_tokens is the provider-reported billable total."
        )
        return {
            "total": total,
            "groups": [
                {
                    "role": key[0],
                    "model": key[1],
                    "reasoning": key[2],
                    **aggregate(items),
                }
                for key, items in sorted(groups.items())
            ],
            "calls": [asdict(call) for call in self._calls],
        }


_ACTIVE_COLLECTOR: contextvars.ContextVar[Optional[LLMUsageCollector]] = (
    contextvars.ContextVar("dialogpilot_llm_usage_collector", default=None)
)


@contextmanager
def capture_llm_usage() -> Iterator[LLMUsageCollector]:
    """为当前异步上下文开启一次隔离计量。"""
    collector = LLMUsageCollector()
    token = _ACTIVE_COLLECTOR.set(collector)
    try:
        yield collector
    finally:
        _ACTIVE_COLLECTOR.reset(token)


async def create_message(
    client: Any,
    profile: ModelProfile,
    role: ModelRole,
    **payload: Any,
) -> Any:
    """执行一次模型调用，并在活动上下文中记录官方 usage。"""
    started = time.perf_counter()
    try:
        response = await client.messages.create(**profile.request(**payload))
    # asyncio.CancelledError 继承 BaseException；它通常代表上层超时取消，
    # 仍属于一次真实供应商调用尝试，必须进入 attempts/error 口径。
    except BaseException as exc:
        collector = _ACTIVE_COLLECTOR.get()
        if collector is not None:
            collector.add(LLMCallUsage(
                role=role.value,
                model=profile.model,
                reasoning=profile.reasoning.value,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=type(exc).__name__,
            ))
        raise

    collector = _ACTIVE_COLLECTOR.get()
    if collector is not None:
        usage = getattr(response, "usage", None)
        content = list(getattr(response, "content", None) or [])
        collector.add(LLMCallUsage(
            role=role.value,
            model=profile.model,
            reasoning=profile.reasoning.value,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_creation_input_tokens=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            thinking_blocks=sum(getattr(block, "type", None) == "thinking" for block in content),
        ))
    return response
