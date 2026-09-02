"""Render the reproducible local cost-policy report."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from application.cost_budget_policy import (
    OFFLINE_KNOWLEDGE_INGEST_BUDGET,
    RouteCostBudgetRegistry,
)


def build_manifest() -> dict[str, object]:
    registry = RouteCostBudgetRegistry()
    policy = {
        "policy_version": registry.version,
        "online_route_budgets": [
            {**asdict(row), "fallback": row.fallback.value}
            for row in registry.all()
        ],
        "offline_ingest_budget": asdict(OFFLINE_KNOWLEDGE_INGEST_BUDGET),
    }
    encoded = json.dumps(
        policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema_version": "local-cost-budget-report-v1",
        "policy": policy,
        "policy_sha256": hashlib.sha256(encoded).hexdigest(),
        "verification": "deterministic-local-test",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_manifest(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
