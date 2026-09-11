#!/usr/bin/env python3
"""Calibrate and replay an Encoder-first, LLM-fallback intent cascade."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.intent_cascade import (  # noqa: E402
    replay_cascade,
    select_threshold_for_precision,
    stratified_group_folds,
)
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
        "--encoder-artifacts",
        type=Path,
        default=ROOT
        / "artifacts/eval/local-encoder-chinese-augmented-2026-08-31",
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=ROOT / "artifacts/eval/intent-weight-calibration-2026-08-31",
    )
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts/eval/encoder-llm-cascade-2026-08-31",
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


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def prediction_map(path: Path) -> dict[str, dict[str, Any]]:
    return {row["case_id"]: row for row in read_jsonl(path)}


def source_map(path: Path) -> dict[str, dict[str, Any]]:
    return {row["case_id"]: row for row in read_jsonl(path)}


def evaluate_split(
    cases: list[dict[str, Any]],
    encoder: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    thresholds: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    labels = [expected(case) for case in cases]
    encoder_predictions = [str(encoder[case["id"]]["predicted"]) for case in cases]
    encoder_confidence = [float(encoder[case["id"]]["confidence"]) for case in cases]
    llm = [sources[case["id"]]["llm"] for case in cases]
    results = {}
    for name, selection in thresholds.items():
        replay = replay_cascade(
            labels,
            encoder_predictions,
            encoder_confidence,
            llm,
            threshold=float(selection["threshold"]),
        )
        details = []
        for index, case in enumerate(cases):
            details.append(
                {
                    "case_id": case["id"],
                    "expected": labels[index],
                    "predicted": replay["predictions"][index],
                    "confidence": round(float(replay["confidences"][index]), 6),
                    "route": replay["route_by_index"][index],
                    "encoder_prediction": encoder_predictions[index],
                    "encoder_confidence": round(float(encoder_confidence[index]), 6),
                    "llm_prediction": str(llm[index].get("intent") or "other"),
                    "correct": labels[index] == replay["predictions"][index],
                }
            )
        results[name] = {
            "selection": selection,
            "metrics": replay["metrics"],
            "routes": replay["routes"],
            "details": details,
        }
    results["encoder_only"] = {
        "metrics": classification_metrics(labels, encoder_predictions, encoder_confidence)
    }
    results["llm_only"] = {
        "metrics": classification_metrics(
            labels,
            [str(item.get("intent") or "other") for item in llm],
            [float(item.get("confidence", 0.0) or 0.0) for item in llm],
        )
    }
    return results


def main() -> int:
    args = parse_args()
    base_rows = read_jsonl(args.base_dataset)
    development = [row for row in base_rows if row.get("split") == "dev"]
    heldout = [row for row in base_rows if row.get("split") == "heldout"]
    augmentation = read_jsonl(args.augmentation)
    combined = development + augmentation
    labels = [expected(case) for case in combined]
    group_ids = [str(case.get("group_id") or case["id"]) for case in combined]
    fold_by_row = stratified_group_folds(labels, group_ids, folds=args.folds)

    encoder = SentenceTransformer(args.model, local_files_only=True)
    embeddings = np.asarray(
        encoder.encode(
            [render_case_text(case) for case in combined],
            batch_size=args.batch_size,
            normalize_embeddings=True,
            show_progress_bar=True,
        ),
        dtype=np.float32,
    )
    config = EncoderClassifierConfig("joint", 10.0)
    oof_predictions = [""] * len(combined)
    oof_confidence = [0.0] * len(combined)
    for fold in range(args.folds):
        train_mask = fold_by_row != fold
        validation_mask = fold_by_row == fold
        model = fit_model(
            embeddings[train_mask],
            [label for index, label in enumerate(labels) if train_mask[index]],
            config,
        )
        predicted, confidence = model.predict(embeddings[validation_mask])
        indices = np.flatnonzero(validation_mask)
        for index, intent, score in zip(indices, predicted, confidence):
            oof_predictions[int(index)] = intent
            oof_confidence[int(index)] = float(score)
    if any(not prediction for prediction in oof_predictions):
        raise RuntimeError("OOF prediction coverage is incomplete")

    targets = {"precision_95": 0.95, "precision_97": 0.97, "precision_99": 0.99}
    thresholds = {
        name: select_threshold_for_precision(
            labels,
            oof_predictions,
            oof_confidence,
            target_accuracy=target,
        )
        for name, target in targets.items()
    }

    verification = read_jsonl(args.verification_dataset)
    diagnostic_predictions = prediction_map(
        args.encoder_artifacts / "diagnostic-predictions.jsonl"
    )
    diagnostic_all = read_jsonl(args.diagnostic_dataset)
    diagnostic = [row for row in diagnostic_all if row["id"] in diagnostic_predictions]
    splits = {
        "heldout": (
            heldout,
            prediction_map(args.encoder_artifacts / "heldout-predictions.jsonl"),
            source_map(args.source_root / "heldout/source-outputs.jsonl"),
        ),
        "verification": (
            verification,
            prediction_map(args.encoder_artifacts / "verification-predictions.jsonl"),
            source_map(args.source_root / "fresh-verification-v3/source-outputs.jsonl"),
        ),
        "multilingual_synthetic_diagnostic": (
            diagnostic,
            diagnostic_predictions,
            source_map(
                ROOT
                / "artifacts/eval/fresh-intent-v3-regression-2026-08-31/source-outputs.jsonl"
            ),
        ),
    }
    evaluated = {
        name: evaluate_split(cases, predictions, sources, thresholds)
        for name, (cases, predictions, sources) in splits.items()
    }

    args.output.mkdir(parents=True, exist_ok=True)
    oof_rows = [
        {
            "case_id": case["id"],
            "group_id": group_ids[index],
            "fold": int(fold_by_row[index]),
            "expected": labels[index],
            "predicted": oof_predictions[index],
            "confidence": round(oof_confidence[index], 6),
            "correct": labels[index] == oof_predictions[index],
        }
        for index, case in enumerate(combined)
    ]
    write_jsonl(args.output / "development-oof-predictions.jsonl", oof_rows)
    for split_name, result in evaluated.items():
        write_jsonl(
            args.output / f"{split_name}-primary-predictions.jsonl",
            result["precision_97"]["details"],
        )

    report = {
        "schema_version": 1,
        "status": "offline_cascade_replay_not_production",
        "contract": {
            "route": "encoder when confidence >= frozen threshold; otherwise full LLM classification",
            "primary_target": "97% OOF accepted accuracy with maximum coverage",
            "rejection_status": "not_typed_other_only",
            "provider_failure": "not_evaluated",
        },
        "development": {
            "case_count": len(combined),
            "group_count": len(set(group_ids)),
            "fold_count": args.folds,
            "encoder_oof_metrics": classification_metrics(
                labels, oof_predictions, oof_confidence
            ),
            "thresholds": thresholds,
        },
        "results": evaluated,
        "inputs": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (
                args.base_dataset,
                args.augmentation,
                args.verification_dataset,
                args.diagnostic_dataset,
                args.encoder_artifacts / "heldout-predictions.jsonl",
                args.encoder_artifacts / "verification-predictions.jsonl",
                args.encoder_artifacts / "diagnostic-predictions.jsonl",
                args.source_root / "heldout/source-outputs.jsonl",
                args.source_root / "fresh-verification-v3/source-outputs.jsonl",
                ROOT
                / "artifacts/eval/fresh-intent-v3-regression-2026-08-31/source-outputs.jsonl",
            )
        },
        "limitations": [
            "Threshold calibration includes LLM-reviewed synthetic Chinese training data, not human gold.",
            "Evaluation sets and LLM captures were consumed by previous experiments.",
            "Heldout LLM captures predate the latest prompt contract and are not a current production comparison.",
            "The replay cannot distinguish out_of_scope from insufficient_context and does not model provider failure.",
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
    summary = {
        "thresholds": thresholds,
        "primary": {
            split: {
                "metrics": result["precision_97"]["metrics"],
                "routes": result["precision_97"]["routes"],
            }
            for split, result in evaluated.items()
        },
        "report": str(report_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
