#!/usr/bin/env python3
"""Train and evaluate a frozen local sentence encoder intent classifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.local_encoder_classifier import (  # noqa: E402
    classification_metrics,
    deterministic_train_validation_split,
    fit_model,
    render_case_text,
    select_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data/eval/intent-weight-calibration-2026-08-31/cases.jsonl",
    )
    parser.add_argument(
        "--verification-dataset",
        type=Path,
        default=ROOT / "data/eval/intent-weight-verification-2026-08-31/cases.jsonl",
    )
    parser.add_argument(
        "--diagnostic-dataset",
        type=Path,
        default=ROOT / "fresh-intent-candidate.jsonl",
    )
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/eval/local-encoder-feasibility-2026-08-31",
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected(case: dict[str, Any]) -> str:
    return str(case["expected"]["intent"])


def encode_cases(encoder: Any, cases: Iterable[dict[str, Any]], batch_size: int) -> np.ndarray:
    return np.asarray(
        encoder.encode(
            [render_case_text(case) for case in cases],
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
        ),
        dtype=np.float32,
    )


def evaluate(model: Any, embeddings: np.ndarray, cases: list[dict[str, Any]]) -> dict[str, Any]:
    labels = [expected(case) for case in cases]
    predictions, confidences = model.predict(embeddings)
    return {
        "metrics": classification_metrics(labels, predictions, confidences),
        "details": [
            {
                "case_id": case["id"],
                "expected": label,
                "predicted": prediction,
                "confidence": round(float(confidence), 6),
                "correct": label == prediction,
            }
            for case, label, prediction, confidence in zip(
                cases, labels, predictions, confidences
            )
        ],
    }


def baseline_metrics(
    cases: list[dict[str, Any]], source_outputs_path: Path
) -> dict[str, dict[str, Any]]:
    if not source_outputs_path.exists():
        return {}
    outputs = {row["case_id"]: row for row in read_jsonl(source_outputs_path)}
    result = {}
    for source in ("llm", "semantic", "ngram", "pattern"):
        aligned = [(case, outputs.get(case["id"])) for case in cases]
        if any(payload is None or source not in payload for _, payload in aligned):
            continue
        labels = [expected(case) for case, _ in aligned]
        predicted = [str(payload[source].get("intent") or "other") for _, payload in aligned]
        confidence = [float(payload[source].get("confidence", 0.0) or 0.0) for _, payload in aligned]
        result[source] = classification_metrics(labels, predicted, confidence)
    return result


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    from sentence_transformers import SentenceTransformer

    all_cases = read_jsonl(args.dataset)
    development = [case for case in all_cases if case.get("split") == "dev"]
    heldout = [case for case in all_cases if case.get("split") == "heldout"]
    verification = read_jsonl(args.verification_dataset)
    diagnostic_all = read_jsonl(args.diagnostic_dataset)
    if not development or not heldout or not verification:
        raise RuntimeError("development, heldout, and verification cases are required")

    started = time.perf_counter()
    encoder = SentenceTransformer(
        args.model, device=args.device, local_files_only=True
    )
    dev_embeddings = encode_cases(encoder, development, args.batch_size)
    heldout_embeddings = encode_cases(encoder, heldout, args.batch_size)
    verification_embeddings = encode_cases(encoder, verification, args.batch_size)

    labels = [expected(case) for case in development]
    supported_labels = set(labels)
    diagnostic = [case for case in diagnostic_all if expected(case) in supported_labels]
    diagnostic_excluded = [
        case for case in diagnostic_all if expected(case) not in supported_labels
    ]
    diagnostic_embeddings = encode_cases(encoder, diagnostic, args.batch_size)
    encoding_seconds = time.perf_counter() - started
    train_indices, validation_indices = deterministic_train_validation_split(labels)
    selected, leaderboard = select_config(
        dev_embeddings[train_indices],
        [labels[index] for index in train_indices],
        dev_embeddings[validation_indices],
        [labels[index] for index in validation_indices],
    )
    final_model = fit_model(dev_embeddings, labels, selected)
    heldout_result = evaluate(final_model, heldout_embeddings, heldout)
    verification_result = evaluate(final_model, verification_embeddings, verification)
    diagnostic_result = evaluate(final_model, diagnostic_embeddings, diagnostic)

    args.output.mkdir(parents=True, exist_ok=True)
    model_path = args.output / "classifier.joblib"
    joblib.dump(
        {
            "schema_version": 1,
            "encoder_model": args.model,
            "config": selected.to_dict(),
            "model": final_model,
        },
        model_path,
    )
    write_jsonl(args.output / "heldout-predictions.jsonl", heldout_result["details"])
    write_jsonl(
        args.output / "verification-predictions.jsonl", verification_result["details"]
    )
    write_jsonl(
        args.output / "diagnostic-predictions.jsonl", diagnostic_result["details"]
    )

    existing_root = ROOT / "artifacts/eval/intent-weight-calibration-2026-08-31"
    report = {
        "schema_version": 1,
        "status": "offline_feasibility_not_production",
        "contract": {
            "known_intents": sorted(set(labels) - {"other"}),
            "oos_label": "other",
            "insufficient_context": "not_evaluated_no_gold_data",
        },
        "protocol": {
            "selection": "deterministic stratified 400 train / 100 validation from dev",
            "final_fit": "all 500 dev cases",
            "heldout": "500 upstream-train heldout cases",
            "verification": "300 independent upstream-test cases",
            "encoder_frozen": True,
            "label_metadata_visible_to_encoder": False,
        },
        "encoder": {
            "model": args.model,
            "device": str(encoder.device),
            "embedding_dimensions": int(dev_embeddings.shape[1]),
            "encoding_seconds_total": round(encoding_seconds, 3),
            "case_count": (
                len(development) + len(heldout) + len(verification) + len(diagnostic)
            ),
        },
        "selection": {
            "candidate_count": len(leaderboard),
            "selected_config": selected.to_dict(),
            "validation_metrics": leaderboard[0]["metrics"],
            "top_candidates": leaderboard[:10],
        },
        "heldout": {
            **heldout_result,
            "captured_baselines": baseline_metrics(
                heldout,
                existing_root / "heldout/source-outputs.jsonl",
            ),
        },
        "verification": {
            **verification_result,
            "captured_baselines": baseline_metrics(
                verification,
                existing_root / "fresh-verification-v3/source-outputs.jsonl",
            ),
        },
        "multilingual_synthetic_diagnostic": {
            **diagnostic_result,
            "status": "diagnostic_only_independently_reviewed_synthetic_previously_consumed",
            "included_case_count": len(diagnostic),
            "excluded_case_count": len(diagnostic_excluded),
            "excluded_labels": sorted(
                {expected(case) for case in diagnostic_excluded}
            ),
            "captured_baselines": baseline_metrics(
                diagnostic,
                ROOT
                / "artifacts/eval/fresh-intent-v3-regression-2026-08-31/source-outputs.jsonl",
            ),
        },
        "inputs": {
            str(args.dataset.relative_to(ROOT)): sha256(args.dataset),
            str(args.verification_dataset.relative_to(ROOT)): sha256(
                args.verification_dataset
            ),
            str(args.diagnostic_dataset.relative_to(ROOT)): sha256(
                args.diagnostic_dataset
            ),
        },
    }
    report_path = args.output / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "files": {
            path.name: sha256(path)
            for path in sorted(args.output.iterdir())
            if path.name != "manifest.json"
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    summary = {
        "selected": selected.to_dict(),
        "validation": leaderboard[0]["metrics"],
        "heldout": heldout_result["metrics"],
        "verification": verification_result["metrics"],
        "multilingual_synthetic_diagnostic": diagnostic_result["metrics"],
        "report": str(report_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
