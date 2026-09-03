#!/usr/bin/env python3
"""Write deterministic P2/P3 diagnostic reports without calling a model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.public_sgd.failure_attribution import attribute_failures
from evaluation.public_sgd.flow_retrieval_eval import evaluate_flow_retrieval
from evaluation.public_sgd.runtime import SgdBenchmarkDataset


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = SgdBenchmarkDataset.load(args.dataset, "dev")
    report = {
        "failure_attribution": attribute_failures(
            args.dataset / "dev" / "cases.jsonl", args.predictions
        ),
        "flow_retrieval_shadow": evaluate_flow_retrieval(dataset),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
