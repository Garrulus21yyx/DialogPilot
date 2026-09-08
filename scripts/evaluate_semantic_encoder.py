"""Compare one frozen semantic candidate with the shipped fast path, without business calls."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import torch

from application.encoder_input import EncoderInput
from evaluation.encoder_fastpath_evaluation import evaluate
from evaluation.semantic_encoder_experiment import SemanticCandidate


async def main(zh: Path, en: Path, output: Path, data: Path, challenge: Path):
    if output.exists():
        raise ValueError("evaluation output exists")
    torch.set_num_threads(2)
    root = Path(__file__).resolve().parents[1]
    sources = {
        "development": [root / data / lang / "heldout.jsonl" for lang in ("zh", "en")],
        "independent": [root / challenge],
        "regression": [root / "data/eval/encoder-language-independent-2026-09-08.jsonl"],
    }
    previous = root / "data/eval/semantic-encoder-context-challenge-2026-09-08.jsonl"
    if (root / challenge) != previous:
        sources["regression"].append(previous)
    consumed = {EncoderInput.from_record(json.loads(line)).identity()
        for path in (root / data).glob("*/*.jsonl") for line in path.read_text().splitlines()}
    leaked = [row["case_id"] for row in map(json.loads, (root / challenge).read_text().splitlines())
              if EncoderInput.from_record(row).identity() in consumed]
    if leaked:
        raise ValueError(f"independent challenge overlaps development data: {leaked}")
    artifacts = {"zh": zh, "en": en}
    baseline = {lang: root / f"artifacts/target-encoder-{lang}-context-v2" for lang in ("zh", "en")}
    output.mkdir(parents=True)
    records = {}
    for scope, paths in sources.items():
        rows = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
        records[scope] = {}
        for name, kwargs in (("baseline", dict(artifacts=baseline)),
                             ("semantic", dict(artifacts=artifacts, load_artifact=SemanticCandidate))):
            result = await evaluate(rows, warmup=True, **kwargs)
            result["sources"] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            for language, summary in result["summary"].items():
                positives = [r for r in result["details"] if r["language"] == language
                             and r["contextual"] and r["expected"] in {"refund_status_summary", "product_identification"}]
                summary["contextual_positive_total"] = len(positives)
                summary["contextual_positive_correct_accepts"] = sum(r["accepted"] and r["correct"] for r in positives)
            (output / f"{scope}-{name}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
            records[scope][name] = result
            print(scope, name, json.dumps(result["summary"], ensure_ascii=False), flush=True)
    adoption = {}
    for lang in artifacts:
        base = records["independent"]["baseline"]["summary"][lang]
        new = records["independent"]["semantic"]["summary"][lang]
        checks = dict(
            precision_at_least_98=all((records[s]["semantic"]["summary"][lang]["accepted_precision"] or 0) >= .98
                                     for s in records),
            no_unsafe_accepts=not any(r["accepted"] and r["expected"] == "__DEFER__"
                for s in records for r in records[s]["semantic"]["details"] if r["language"] == lang),
            contextual_improvement=new["contextual_positive_correct_accepts"] > base["contextual_positive_correct_accepts"],
            overall_coverage_not_lower=new["correct"] >= base["correct"],
            cpu_p95_under_100ms=new["encoder_p95_ms"] <= 100,
        )
        adoption[lang] = dict(passed=all(checks.values()), checks=checks,
            artifact_sha256=hashlib.sha256((artifacts[lang] / "manifest.json").read_bytes()).hexdigest())
    (output / "adoption.json").write_text(json.dumps(adoption, indent=2) + "\n")
    print(json.dumps(adoption), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zh", type=Path, required=True)
    parser.add_argument("--en", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data", type=Path, default=Path("data/training/semantic-encoder-v1"))
    parser.add_argument("--challenge", type=Path, default=Path("data/eval/semantic-encoder-context-challenge-2026-09-08.jsonl"))
    asyncio.run(main(**vars(parser.parse_args())))
