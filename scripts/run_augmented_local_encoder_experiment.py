#!/usr/bin/env python3
"""A/B test the frozen encoder with reviewed Chinese training augmentation."""
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
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.local_encoder_classifier import (  # noqa: E402
    EncoderClassifierConfig,
    classification_metrics,
    fit_model,
    render_case_text,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-dataset",
        type=Path,
        default=ROOT / "data/eval/intent-weight-calibration-2026-08-31/cases.jsonl",
    )
    parser.add_argument(
        "--augmentation",
        type=Path,
        default=ROOT
        / "data/training/intent-chinese-augmentation-2026-08-31/cases.jsonl",
    )
    parser.add_argument(
        "--verification-dataset",
        type=Path,
        default=ROOT / "data/eval/intent-weight-verification-2026-08-31/cases.jsonl",
    )
    parser.add_argument(
        "--diagnostic-dataset", type=Path, default=ROOT / "fresh-intent-candidate.jsonl"
    )
    parser.add_argument(
        "--baseline-report",
        type=Path,
        default=ROOT
        / "artifacts/eval/local-encoder-feasibility-2026-08-31/report.json",
    )
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--device", default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "artifacts/eval/local-encoder-chinese-augmented-2026-08-31",
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


def encode_cases(
    encoder: SentenceTransformer, cases: list[dict[str, Any]], batch_size: int
) -> np.ndarray:
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
    predicted, confidences = model.predict(embeddings)
    return {
        "metrics": classification_metrics(labels, predicted, confidences),
        "details": [
            {
                "case_id": case["id"],
                "expected": label,
                "predicted": prediction,
                "confidence": round(float(confidence), 6),
                "correct": label == prediction,
            }
            for case, label, prediction, confidence in zip(
                cases, labels, predicted, confidences
            )
        ],
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def metric_delta(after: dict[str, Any], before: dict[str, Any]) -> dict[str, float]:
    keys = ("accuracy", "macro_f1", "oos_precision", "oos_recall")
    return {key: round(float(after[key]) - float(before[key]), 6) for key in keys}


def main() -> int:
    args = parse_args()
    baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
    config = EncoderClassifierConfig(**baseline["selection"]["selected_config"])

    base_rows = read_jsonl(args.base_dataset)
    development = [case for case in base_rows if case.get("split") == "dev"]
    heldout = [case for case in base_rows if case.get("split") == "heldout"]
    augmentation = read_jsonl(args.augmentation)
    verification = read_jsonl(args.verification_dataset)
    diagnostic_all = read_jsonl(args.diagnostic_dataset)
    supported = {expected(case) for case in development}
    if any(expected(case) not in supported for case in augmentation):
        raise RuntimeError("augmentation contains a label outside the base classifier algebra")
    diagnostic = [case for case in diagnostic_all if expected(case) in supported]

    diagnostic_normalized = {
        "".join(character.lower() for character in case["input"]["message"] if character.isalnum())
        for case in diagnostic_all
    }
    for case in augmentation:
        normalized = "".join(
            character.lower() for character in case["input"]["message"] if character.isalnum()
        )
        if normalized in diagnostic_normalized:
            raise RuntimeError(f"augmentation leaks an exact diagnostic text: {case['id']}")

    started = time.perf_counter()
    encoder = SentenceTransformer(args.model, device=args.device, local_files_only=True)
    training_cases = development + augmentation
    training_embeddings = encode_cases(encoder, training_cases, args.batch_size)
    heldout_embeddings = encode_cases(encoder, heldout, args.batch_size)
    verification_embeddings = encode_cases(encoder, verification, args.batch_size)
    diagnostic_embeddings = encode_cases(encoder, diagnostic, args.batch_size)
    encoding_seconds = time.perf_counter() - started

    model = fit_model(
        training_embeddings,
        [expected(case) for case in training_cases],
        config,
    )
    heldout_result = evaluate(model, heldout_embeddings, heldout)
    verification_result = evaluate(model, verification_embeddings, verification)
    diagnostic_result = evaluate(model, diagnostic_embeddings, diagnostic)

    args.output.mkdir(parents=True, exist_ok=True)
    model_path = args.output / "classifier.joblib"
    joblib.dump(
        {
            "schema_version": 1,
            "encoder_model": args.model,
            "config": config.to_dict(),
            "model": model,
            "training_case_count": len(training_cases),
            "augmentation_sha256": sha256(args.augmentation),
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

    report = {
        "schema_version": 1,
        "status": "offline_augmented_candidate_not_production",
        "config": config.to_dict(),
        "protocol": {
            "config_source": str(args.baseline_report.relative_to(ROOT)),
            "config_frozen_before_augmentation_evaluation": True,
            "base_training_cases": len(development),
            "reviewed_synthetic_chinese_training_cases": len(augmentation),
            "heldout_cases": len(heldout),
            "verification_cases": len(verification),
            "multilingual_synthetic_diagnostic_cases": len(diagnostic),
            "diagnostic_used_for_training_or_selection": False,
            "encoder_frozen": True,
        },
        "encoder": {
            "model": args.model,
            "device": str(encoder.device),
            "embedding_dimensions": int(training_embeddings.shape[1]),
            "encoding_seconds_total": round(encoding_seconds, 3),
        },
        "heldout": {
            **heldout_result,
            "baseline_metrics": baseline["heldout"]["metrics"],
            "delta": metric_delta(
                heldout_result["metrics"], baseline["heldout"]["metrics"]
            ),
        },
        "verification": {
            **verification_result,
            "baseline_metrics": baseline["verification"]["metrics"],
            "delta": metric_delta(
                verification_result["metrics"], baseline["verification"]["metrics"]
            ),
        },
        "multilingual_synthetic_diagnostic": {
            **diagnostic_result,
            "status": "diagnostic_only_no_human_gold_previously_consumed",
            "baseline_metrics": baseline["multilingual_synthetic_diagnostic"]["metrics"],
            "delta": metric_delta(
                diagnostic_result["metrics"],
                baseline["multilingual_synthetic_diagnostic"]["metrics"],
            ),
        },
        "inputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (
                args.base_dataset,
                args.augmentation,
                args.verification_dataset,
                args.diagnostic_dataset,
                args.baseline_report,
            )
        },
        "limitations": [
            "Chinese augmentation is LLM-reviewed synthetic data, not human gold.",
            "All evaluation sets were previously consumed by earlier experiments.",
            "The classifier still represents OOS and insufficient context with one other label.",
        ],
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
    print(
        json.dumps(
            {
                "config": config.to_dict(),
                "heldout": heldout_result["metrics"],
                "verification": verification_result["metrics"],
                "multilingual_synthetic_diagnostic": diagnostic_result["metrics"],
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
