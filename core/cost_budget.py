"""Request-scoped cost budgets and provider-usage reconciliation.

The tracker owns resource accounting only. Route policy supplies the bounded limits
and the Application boundary owns the typed fallback when a limit is exhausted.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Iterator, Mapping, Optional


class BudgetFallback(str, Enum):
    RULE_CLARIFY = "rule_clarify"
    HANDOFF = "handoff"
    ABSTAIN = "abstain"


@dataclass(frozen=True)
class RouteCostBudget:
    route_mode: str
    max_model_calls: int
    max_tool_calls: int
    max_retrieval_calls: int
    max_input_tokens: int
    max_output_tokens: int
    fallback: BudgetFallback
    policy_version: str

    def __post_init__(self) -> None:
        values = (
            self.max_model_calls, self.max_tool_calls, self.max_retrieval_calls,
            self.max_input_tokens, self.max_output_tokens,
        )
        if not self.route_mode or not self.policy_version or any(value < 0 for value in values):
            raise ValueError("route cost budget is invalid")


@dataclass(frozen=True)
class OfflineIngestBudget:
    max_sources_per_batch: int
    max_source_bytes: int
    max_total_source_bytes: int
    max_chunks_per_batch: int
    max_embedding_tokens_per_batch: int
    policy_version: str

    def __post_init__(self) -> None:
        if min(
            self.max_sources_per_batch,
            self.max_source_bytes,
            self.max_total_source_bytes,
            self.max_chunks_per_batch,
            self.max_embedding_tokens_per_batch,
        ) < 1 or not self.policy_version:
            raise ValueError("offline ingest budget is invalid")


class OfflineIngestBudgetExceeded(RuntimeError):
    def __init__(self, *, dimension: str, observed: int, limit: int, policy_version: str):
        self.code = "OFFLINE_INGEST_BUDGET_EXHAUSTED"
        self.dimension = dimension
        self.observed = int(observed)
        self.limit = int(limit)
        self.policy_version = policy_version
        super().__init__(f"offline ingest {dimension}={observed} exceeds {limit}")


@dataclass
class CostUsage:
    model_calls: int = 0
    tool_calls: int = 0
    retrieval_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


class RouteBudgetExceeded(RuntimeError):
    """Typed fail-closed result for one exhausted resource dimension."""

    def __init__(
        self, *, route_mode: str, dimension: str, observed: int, limit: int,
        fallback: BudgetFallback, policy_version: str,
    ):
        self.route_mode = route_mode
        self.dimension = dimension
        self.observed = int(observed)
        self.limit = int(limit)
        self.fallback = fallback
        self.policy_version = policy_version
        super().__init__(
            f"route budget exhausted: {dimension}={observed} exceeds {limit}"
        )

    @property
    def code(self) -> str:
        return "ROUTE_COST_BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class RouteBudgetExhaustedOutcome:
    code: str
    route_mode: str
    dimension: str
    observed: int
    limit: int
    fallback: BudgetFallback
    policy_version: str
    usage: Mapping[str, int]
    provider_usage: Mapping[str, object] = field(default_factory=dict)


class RouteBudgetTracker:
    def __init__(self, budget: RouteCostBudget):
        self.budget = budget
        self.usage = CostUsage()
        self._exhausted: Optional[RouteBudgetExceeded] = None

    @property
    def exhausted(self) -> Optional[RouteBudgetExceeded]:
        return self._exhausted

    def before_model_call(self) -> None:
        self._increment("model_calls", self.budget.max_model_calls)

    def before_tool_call(self) -> None:
        self._increment("tool_calls", self.budget.max_tool_calls)

    def before_retrieval(self) -> None:
        self._increment("retrieval_calls", self.budget.max_retrieval_calls)

    def record_provider_tokens(self, *, input_tokens: int, output_tokens: int) -> None:
        self.usage.input_tokens += max(0, int(input_tokens))
        self.usage.output_tokens += max(0, int(output_tokens))
        self._mark_if_over("input_tokens", self.budget.max_input_tokens)
        self._mark_if_over("output_tokens", self.budget.max_output_tokens)

    def raise_if_exhausted(self) -> None:
        if self._exhausted is not None:
            raise self._exhausted

    def outcome(self) -> Optional[RouteBudgetExhaustedOutcome]:
        if self._exhausted is None:
            return None
        error = self._exhausted
        return RouteBudgetExhaustedOutcome(
            code=error.code,
            route_mode=error.route_mode,
            dimension=error.dimension,
            observed=error.observed,
            limit=error.limit,
            fallback=error.fallback,
            policy_version=error.policy_version,
            usage=self.usage.to_dict(),
        )

    def _increment(self, dimension: str, limit: int) -> None:
        if self._exhausted is not None:
            raise self._exhausted
        value = int(getattr(self.usage, dimension)) + 1
        setattr(self.usage, dimension, value)
        self._mark_if_over(dimension, limit)
        self.raise_if_exhausted()

    def _mark_if_over(self, dimension: str, limit: int) -> None:
        observed = int(getattr(self.usage, dimension))
        if observed > limit and self._exhausted is None:
            self._exhausted = RouteBudgetExceeded(
                route_mode=self.budget.route_mode,
                dimension=dimension,
                observed=observed,
                limit=limit,
                fallback=self.budget.fallback,
                policy_version=self.budget.policy_version,
            )


_ACTIVE_TRACKER: contextvars.ContextVar[Optional[RouteBudgetTracker]] = (
    contextvars.ContextVar("dialogpilot_route_cost_budget", default=None)
)


@contextmanager
def enforce_route_budget(budget: RouteCostBudget) -> Iterator[RouteBudgetTracker]:
    tracker = RouteBudgetTracker(budget)
    token = _ACTIVE_TRACKER.set(tracker)
    try:
        yield tracker
    finally:
        _ACTIVE_TRACKER.reset(token)


def active_route_budget() -> Optional[RouteBudgetTracker]:
    return _ACTIVE_TRACKER.get()


@dataclass(frozen=True)
class ProviderUsageSample:
    calls: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class UsageReconciliation:
    matches: bool
    call_delta: int
    input_token_delta: int
    output_token_delta: int


def reconcile_provider_usage(
    captured: ProviderUsageSample,
    billed: ProviderUsageSample,
) -> UsageReconciliation:
    result = UsageReconciliation(
        matches=captured == billed,
        call_delta=captured.calls - billed.calls,
        input_token_delta=captured.input_tokens - billed.input_tokens,
        output_token_delta=captured.output_tokens - billed.output_tokens,
    )
    return result
