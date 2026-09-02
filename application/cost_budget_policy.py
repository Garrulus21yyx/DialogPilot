"""Versioned Platform/Application cost policy for online routes and offline ingest."""
from __future__ import annotations

from core.cost_budget import BudgetFallback, OfflineIngestBudget, RouteCostBudget
from application.route_decision import RouteMode


class RouteCostBudgetRegistry:
    """Close the supported RouteMode budget algebra with one versioned policy."""

    version = "route-cost-budget-v1"

    def __init__(self) -> None:
        # Limits derive from the route component algebra and the M2 execution caps:
        # ReAct=4 steps, at most 3 executed workers, optional synthesis/verifier.
        rows = {
            RouteMode.DIRECT: (0, 0, 0, 0, 0, BudgetFallback.RULE_CLARIFY),
            RouteMode.KNOWLEDGE_QA: (2, 0, 1, 24000, 2048, BudgetFallback.ABSTAIN),
            RouteMode.AGENT_TASK: (5, 4, 4, 60000, 5120, BudgetFallback.HANDOFF),
            RouteMode.MIXED: (5, 4, 5, 60000, 5120, BudgetFallback.HANDOFF),
            RouteMode.MULTI_DOMAIN: (14, 12, 12, 168000, 14336, BudgetFallback.HANDOFF),
            RouteMode.CLARIFY: (0, 0, 0, 0, 0, BudgetFallback.RULE_CLARIFY),
            RouteMode.HANDOFF: (0, 0, 0, 0, 0, BudgetFallback.HANDOFF),
            RouteMode.OUT_OF_SCOPE: (0, 0, 0, 0, 0, BudgetFallback.RULE_CLARIFY),
        }
        self._budgets = {
            mode: RouteCostBudget(
                route_mode=mode.value,
                max_model_calls=row[0], max_tool_calls=row[1],
                max_retrieval_calls=row[2], max_input_tokens=row[3],
                max_output_tokens=row[4], fallback=row[5],
                policy_version=self.version,
            )
            for mode, row in rows.items()
        }
        if set(self._budgets) != set(RouteMode):
            raise ValueError("route cost budget registry is incomplete")

    def for_route(self, mode: RouteMode) -> RouteCostBudget:
        try:
            return self._budgets[mode]
        except KeyError as exc:
            raise ValueError(f"unsupported route cost budget: {mode!r}") from exc

    def all(self) -> tuple[RouteCostBudget, ...]:
        return tuple(self._budgets[mode] for mode in RouteMode)


OFFLINE_KNOWLEDGE_INGEST_BUDGET = OfflineIngestBudget(
    max_sources_per_batch=256,
    max_source_bytes=10 * 1024 * 1024,
    max_total_source_bytes=10 * 1024 * 1024,
    max_chunks_per_batch=4096,
    max_embedding_tokens_per_batch=2_000_000,
    policy_version="offline-knowledge-ingest-budget-v1",
)
