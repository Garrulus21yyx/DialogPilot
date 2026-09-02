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
from core.cost_budget import RouteBudgetExceeded, active_route_budget
from core.provider_context_budget import DEFAULT_PROVIDER_CONTEXT_BUDGET
from core.provider_cache_policy import (
    DEFAULT_PROVIDER_CACHE_GATE,
    ProviderCacheInvocation,
    ProviderCacheStatus,
)
from core.tracing import active_trace_recorder


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
    provider_request_id: str = ""
    estimated_input_tokens: int = 0
    max_context_tokens: int = 0
    reserved_output_tokens: int = 0
    cache_policy_status: str = ProviderCacheStatus.DISABLED.value
    cache_policy_reason: str = "not_requested"


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
            estimated_input = sum(item.estimated_input_tokens for item in items)
            actual_input = sum(item.input_tokens for item in items)
            context_utilization = [
                (item.estimated_input_tokens + item.reserved_output_tokens)
                / item.max_context_tokens
                for item in items if item.max_context_tokens
            ]
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
                "estimated_input_tokens": estimated_input,
                "input_estimation_ratio": (
                    round(estimated_input / actual_input, 4)
                    if actual_input and estimated_input else None
                ),
                "max_estimated_context_utilization": (
                    round(max(context_utilization), 6)
                    if context_utilization else None
                ),
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


def record_external_llm_run(
    profile: ModelProfile,
    role: ModelRole,
    usage: Any,
    *,
    latency_ms: float,
    error: Optional[str] = None,
) -> None:
    """Project an externally managed model run into DialogPilot's usage contract.

    PydanticAI owns output-correction retries, so those requests do not pass through
    :func:`create_message`. Its run usage exposes exact aggregate request/token
    counts but not per-request latency. We preserve exact totals and distribute the
    wall time evenly across attempts so aggregate latency remains authoritative.
    """
    collector = _ACTIVE_COLLECTOR.get()
    if collector is None:
        return
    requests = int(getattr(usage, "requests", 0) or 0)
    if requests < 1:
        requests = 1 if error else 0
    if requests == 0:
        return

    budget_tracker = active_route_budget()
    if budget_tracker is not None:
        for _ in range(requests):
            try:
                budget_tracker.before_model_call()
            except RouteBudgetExceeded:
                # The external run has already happened. Preserve exact provider
                # usage and let the Application boundary return the typed outcome.
                break

    def split(value: int) -> list[int]:
        quotient, remainder = divmod(max(0, int(value)), requests)
        return [quotient + int(index < remainder) for index in range(requests)]

    input_tokens = split(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = split(getattr(usage, "output_tokens", 0) or 0)
    cache_write = split(getattr(usage, "cache_write_tokens", 0) or 0)
    cache_read = split(getattr(usage, "cache_read_tokens", 0) or 0)
    per_request_latency = max(0.0, float(latency_ms)) / requests
    for index in range(requests):
        if budget_tracker is not None:
            budget_tracker.record_provider_tokens(
                input_tokens=input_tokens[index], output_tokens=output_tokens[index],
            )
        collector.add(LLMCallUsage(
            role=role.value,
            model=profile.model,
            reasoning=profile.reasoning.value,
            latency_ms=per_request_latency,
            input_tokens=input_tokens[index],
            output_tokens=output_tokens[index],
            cache_creation_input_tokens=cache_write[index],
            cache_read_input_tokens=cache_read[index],
            error=error if index == requests - 1 else None,
        ))


async def create_message(
    client: Any,
    profile: ModelProfile,
    role: ModelRole,
    **payload: Any,
) -> Any:
    """执行一次模型调用，并在活动上下文中记录官方 usage。"""
    cache_invocation = payload.pop("provider_cache", None)
    if cache_invocation is not None and not isinstance(
        cache_invocation, ProviderCacheInvocation,
    ):
        raise TypeError("provider_cache must be a ProviderCacheInvocation")
    cache_payload, cache_decision = (
        DEFAULT_PROVIDER_CACHE_GATE.apply(payload, cache_invocation)
        if cache_invocation is not None
        else (payload, None)
    )
    request = profile.request(**cache_payload)
    context_usage = DEFAULT_PROVIDER_CONTEXT_BUDGET.validate(profile, role, request)
    budget_tracker = active_route_budget()
    if budget_tracker is not None:
        budget_tracker.before_model_call()
    started = time.perf_counter()
    try:
        response = await _observed_provider_call(client, request, profile, role)
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
                estimated_input_tokens=context_usage.estimated_input_tokens,
                max_context_tokens=context_usage.max_context_tokens,
                reserved_output_tokens=context_usage.output_reserve_tokens,
                cache_policy_status=(
                    cache_decision.status.value if cache_decision
                    else ProviderCacheStatus.DISABLED.value
                ),
                cache_policy_reason=(
                    cache_decision.reason if cache_decision else "not_requested"
                ),
            ))
        raise

    collector = _ACTIVE_COLLECTOR.get()
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if budget_tracker is not None:
        budget_tracker.record_provider_tokens(
            input_tokens=input_tokens, output_tokens=output_tokens,
        )
    if collector is not None:
        content = list(getattr(response, "content", None) or [])
        collector.add(LLMCallUsage(
            role=role.value,
            model=profile.model,
            reasoning=profile.reasoning.value,
            latency_ms=(time.perf_counter() - started) * 1000,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            thinking_blocks=sum(getattr(block, "type", None) == "thinking" for block in content),
            provider_request_id=str(getattr(response, "id", "") or "")[:160],
            estimated_input_tokens=context_usage.estimated_input_tokens,
            max_context_tokens=context_usage.max_context_tokens,
            reserved_output_tokens=context_usage.output_reserve_tokens,
            cache_policy_status=(
                cache_decision.status.value if cache_decision
                else ProviderCacheStatus.DISABLED.value
            ),
            cache_policy_reason=(
                cache_decision.reason if cache_decision else "not_requested"
            ),
        ))
    return response


async def _observed_provider_call(
    client: Any, request: Dict[str, Any], profile: ModelProfile, role: ModelRole,
) -> Any:
    recorder = active_trace_recorder()
    if recorder is None:
        return await client.messages.create(**request)
    with recorder.span(
        "llm.generate",
        kind="llm",
        attributes={
            "model": profile.model,
            "model_role": role.value,
            "reasoning": profile.reasoning.value,
        },
    ) as observation:
        response = await client.messages.create(**request)
        usage = getattr(response, "usage", None)
        observation.set_attributes(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            provider_request_id=str(getattr(response, "id", "") or "")[:160],
        )
        return response
