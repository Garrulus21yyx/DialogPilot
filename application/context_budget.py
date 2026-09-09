"""Deterministic per-model-call context budgeting without mutating memory."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from core.token_estimator import TokenEstimator


class ModelContextBudgetExceeded(ValueError):
    def __init__(self, required_tokens: int, available_tokens: int) -> None:
        self.required_tokens = required_tokens
        self.available_tokens = available_tokens
        super().__init__(
            f"model context requires {required_tokens} tokens; "
            f"available input budget is {available_tokens}"
        )


@dataclass(frozen=True)
class ContextBuildReport:
    policy_version: str
    original_tokens: int
    final_tokens: int
    available_tokens: int
    removed_items: tuple[str, ...] = ()
    externalized_items: tuple[str, ...] = ()


@dataclass(frozen=True)
class BudgetedPayload:
    payload: Mapping[str, Any]
    report: ContextBuildReport


class ContextBudgetManager:
    """Fit one provider input; persistent Transcript/Summary remain untouched."""

    version = "model-context-budget-v1"

    def __init__(
        self,
        *,
        context_window_tokens: int = 16000,
        reserved_output_tokens: int = 1200,
        protocol_reserve_tokens: int = 600,
    ) -> None:
        available = (
            int(context_window_tokens)
            - int(reserved_output_tokens)
            - int(protocol_reserve_tokens)
        )
        if available < 1:
            raise ValueError("model context reserves leave no input budget")
        self.available_tokens = available
        self._estimator = TokenEstimator()

    def fit_payload(
        self,
        payload: Mapping[str, Any],
        *,
        trim_oldest_paths: Sequence[str] = (),
        token_counter: Callable[[Mapping[str, Any]], int] | None = None,
        can_trim: Callable[[str, Any], bool] | None = None,
    ) -> BudgetedPayload:
        """Trim declared chronological lists only; never invent summary content."""
        value = copy.deepcopy(dict(payload))
        measure = token_counter or self._estimate
        original = measure(value)
        removed: list[str] = []
        while measure(value) > self.available_tokens:
            changed = False
            for path in trim_oldest_paths:
                items = self._path(value, path)
                if isinstance(items, list) and items and (can_trim is None or can_trim(path, items[0])):
                    items.pop(0)
                    removed.append(f"{path}[oldest]")
                    changed = True
                    break
            if not changed:
                required = measure(value)
                raise ModelContextBudgetExceeded(
                    required, self.available_tokens,
                )
        final = measure(value)
        return BudgetedPayload(
            value,
            ContextBuildReport(
                self.version, original, final, self.available_tokens,
                tuple(removed), (),
            ),
        )

    def _estimate(self, value: Any) -> int:
        return self._estimator.estimate(json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ))

    @staticmethod
    def _path(value: dict[str, Any], path: str) -> Any:
        current: Any = value
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current
