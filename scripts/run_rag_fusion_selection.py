#!/usr/bin/env python3
"""Compare inexpensive adaptive fusion using frozen development captures."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.rag_fusion_selection import evaluate_selection
from evaluation.rag_provider_free import file_sha

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--capture", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    report_path = args.capture.parent / "report.json"
    report = json.loads(report_path.read_text())
    if (
        not report.get("configuration_selection_allowed")
        or report.get("status") != "DEVELOPMENT_DIAGNOSTIC"
    ):
        raise ValueError("requires development capture")
    result = evaluate_selection(
        [json.loads(line) for line in args.capture.read_text().splitlines()]
    )
    result["input_sha256"] = file_sha(args.capture)
    result["report_sha256"] = file_sha(report_path)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result["metrics"], ensure_ascii=False))
