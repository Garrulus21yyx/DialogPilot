#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation.rag_acceptance_decision import decide
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import file_sha

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise ValueError("output must be new")
    contract_path = args.root / "acceptance-contract.json"
    contract = json.loads(contract_path.read_text())
    run = args.root / "run"
    report = json.loads((run / "report.json").read_text())
    if report.get("acceptance_contract_sha256") != file_sha(
        contract_path
    ) or report.get("configuration_selection_allowed"):
        raise ValueError("acceptance contract binding invalid")
    if (
        report["external_inference_api_calls"] != 0
        or report["projection"]["evidence_containment_rate"] != 1
    ):
        raise ValueError("acceptance resource/containment gate failed")
    dataset = RagDataset.load(args.root / "dataset")
    before = [json.loads(l) for l in (run / "cases.jsonl").read_text().splitlines()]
    after = [
        json.loads(l) for l in (run / "reranked-cases.jsonl").read_text().splitlines()
    ]
    result = decide(dataset, before, after, contract)
    result["report_sha256"] = file_sha(run / "report.json")
    result["contract_sha256"] = file_sha(contract_path)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in {"cases", "rescued", "harmed"}
            },
            ensure_ascii=False,
        )
    )
