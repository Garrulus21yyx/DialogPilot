"""Dev-only, constraint-first configuration selection."""
from __future__ import annotations

import random
import statistics
from collections import defaultdict
from typing import Any, Mapping, Sequence


def select_configuration(
    rows: Sequence[Mapping[str, Any]],
    *,
    split: str,
    hard_constraints: Mapping[str, tuple[str, float]],
    metric_priority: Sequence[tuple[str, str]],
    cost_priority: Sequence[str] = (),
) -> dict[str, Any]:
    """Select by safety constraints then lexicographic quality and inverse cost.

    Constraint operators are ``eq``, ``ge`` and ``le``. Metric directions are
    ``max`` and ``min``. Heldout is report-only and cannot recommend a config.
    """
    if split not in {"dev", "heldout", "stress"}:
        raise ValueError("unsupported split")

    def passes(row: Mapping[str, Any]) -> bool:
        for metric, (operator, target) in hard_constraints.items():
            value = float(row.get(metric, float("nan")))
            if operator == "eq" and value != target:
                return False
            if operator == "ge" and value < target:
                return False
            if operator == "le" and value > target:
                return False
            if operator not in {"eq", "ge", "le"}:
                raise ValueError(f"unsupported constraint operator: {operator}")
        return True

    eligible = [dict(row) for row in rows if passes(row)]

    def key(row: Mapping[str, Any]) -> tuple[float, ...]:
        quality = tuple(
            float(row.get(metric, 0.0)) * (1.0 if direction == "max" else -1.0)
            for metric, direction in metric_priority
        )
        costs = tuple(-float(row.get(metric, 0.0)) for metric in cost_priority)
        return (*quality, *costs)

    for _metric, direction in metric_priority:
        if direction not in {"max", "min"}:
            raise ValueError(f"unsupported metric direction: {direction}")
    ranked = sorted(eligible, key=lambda row: (key(row), str(row.get("config_id", ""))), reverse=True)
    return {
        "split": split,
        "selection_allowed": split == "dev",
        "hard_constraints": dict(hard_constraints),
        "metric_priority": list(metric_priority),
        "eligible_count": len(eligible),
        "recommended": (
            ranked[0].get("config_id") if split == "dev" and ranked else None
        ),
        "ranked": ranked,
    }


def paired_group_bootstrap_delta(
    candidate: Sequence[Mapping[str, float]],
    baseline: Sequence[Mapping[str, float]],
    *,
    group_ids: Sequence[str],
    metric: str,
    samples: int = 1000,
    seed: int = 0,
) -> dict[str, float]:
    """Estimate a paired metric delta CI without splitting semantic groups."""
    if len(candidate) != len(baseline) or len(candidate) != len(group_ids):
        raise ValueError("paired bootstrap inputs must have equal lengths")
    if samples < 1:
        raise ValueError("bootstrap samples must be positive")
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, group_id in enumerate(group_ids):
        grouped[str(group_id)].append(index)
    groups = sorted(grouped)
    if not groups:
        return {"delta": 0.0, "ci95_low": 0.0, "ci95_high": 0.0, "samples": float(samples)}

    def delta(indices: Sequence[int]) -> float:
        return statistics.fmean(
            float(candidate[index][metric]) - float(baseline[index][metric])
            for index in indices
        )

    observed = delta(range(len(candidate)))
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sampled_groups = [rng.choice(groups) for _ in groups]
        indices = [index for group in sampled_groups for index in grouped[group]]
        draws.append(delta(indices))
    draws.sort()
    lower_index = max(0, int(samples * 0.025) - 1)
    upper_index = min(samples - 1, int(samples * 0.975))
    return {
        "delta": observed,
        "ci95_low": draws[lower_index],
        "ci95_high": draws[upper_index],
        "samples": float(samples),
    }
