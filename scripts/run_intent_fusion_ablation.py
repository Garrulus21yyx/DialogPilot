#!/usr/bin/env python3
"""Capture intent sources once, then replay compact fusion ablations offline."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from core.intent_fusion_v2 import extract_pattern_evidence
from core.intent_recognizer import IntentCategory, IntentRecognizer, _SEMANTIC_PROTOTYPES
from core.model_policy import ModelPolicy, ModelRole
from evaluation.intent_fusion_ablation import (
    build_cases,
    load_external_intent_cases,
    load_reviewed_candidate_cases,
    sealed_validation_report,
    summary_report,
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    intent = payload.get("intent", IntentCategory.OTHER)
    if isinstance(intent, IntentCategory):
        intent = intent.value
    return {
        "intent": str(intent),
        "confidence": float(payload.get("confidence", 0.0) or 0.0),
        "failed": bool(payload.get("failed", False)),
        "reasoning": str(payload.get("reasoning", "")),
    }


def _pattern_evidence_payload(message: str) -> list[dict[str, Any]]:
    return [
        {
            "intent": item.intent.value,
            "keyword": item.keyword,
            "span": item.span,
            "start": item.start,
            "end": item.end,
            "polarity": item.polarity.value,
            "specific": item.specific,
        }
        for item in extract_pattern_evidence(message)
    ]


async def _capture_llm_and_local(
    cases: list[dict[str, Any]],
    existing: dict[str, dict[str, Any]],
    *,
    concurrency: int,
) -> dict[str, dict[str, Any]]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is required for live source capture")
    policy = ModelPolicy.from_env()
    profile = policy.profile(ModelRole.INTENT)
    recognizer = IntentRecognizer(
        api_key=key,
        base_url=policy.base_url,
        model=profile.model,
        similarity_mode="ngram",
        model_profile=profile,
    )
    classifier_fingerprint = recognizer.classifier_fingerprint()
    semaphore = asyncio.Semaphore(concurrency)

    async def capture(case: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        case_id = case["case_id"]
        cached = dict(existing.get(case_id) or {})
        input_fingerprint = hashlib.sha256(json.dumps(
            {"message": case["message"], "history": case.get("history") or []},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")).hexdigest()
        if cached.get("input_sha256") != input_fingerprint:
            cached = {}
        elif cached.get("classifier_sha256") != classifier_fingerprint:
            # Semantic prototypes have their own persisted template artifact and
            # may be reused when only the LLM/ngram/pattern classifier changes.
            cached = {"semantic": cached["semantic"]} if "semantic" in cached else {}
        cached["input_sha256"] = input_fingerprint
        cached["classifier_sha256"] = classifier_fingerprint
        cached["pattern_evidence"] = _pattern_evidence_payload(case["message"])
        if {"llm", "ngram", "pattern"}.issubset(cached):
            return case_id, cached
        async with semaphore:
            llm, ngram = await asyncio.gather(
                recognizer._llm_recognize(case["message"], history=case.get("history") or None),
                recognizer._embedding_recognize(case["message"]),
            )
        cached.update({
            "case_id": case_id,
            "llm": _source_payload(llm),
            "ngram": _source_payload(ngram),
            "pattern": _source_payload(recognizer._pattern_recognize(case["message"])),
        })
        print(f"captured {case_id}", flush=True)
        return case_id, cached

    captured = await asyncio.gather(*(capture(case) for case in cases))
    return dict(captured)


def _capture_semantic(
    cases: list[dict[str, Any]],
    outputs: dict[str, dict[str, Any]],
    *,
    model_name: str,
    batch_size: int,
) -> None:
    missing = [case for case in cases if "semantic" not in outputs[case["case_id"]]]
    if not missing:
        return
    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(model_name, local_files_only=True)
    labels: list[IntentCategory] = []
    texts: list[str] = []
    for label, templates in _SEMANTIC_PROTOTYPES.items():
        for template in templates:
            labels.append(label)
            texts.append(template)
    template_vectors = model.encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
    )
    message_vectors = model.encode(
        [case["message"] for case in missing],
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    similarities = np.asarray(message_vectors) @ np.asarray(template_vectors).T
    for case, row in zip(missing, similarities):
        best_by_intent: dict[IntentCategory, tuple[float, int]] = {}
        for index, (label, score) in enumerate(zip(labels, row)):
            previous = best_by_intent.get(label)
            if previous is None or float(score) > previous[0]:
                best_by_intent[label] = (float(score), index)
        ranked = sorted(
            best_by_intent.items(), key=lambda item: (-item[1][0], item[0].value),
        )
        (top1_label, (top1_score, top1_index)), (top2_label, (top2_score, _)) = ranked[:2]
        outputs[case["case_id"]]["semantic"] = {
            "intent": top1_label.value,
            "confidence": top1_score,
            "second_intent": top2_label.value,
            "second_confidence": top2_score,
            "margin": top1_score - top2_score,
            "failed": False,
            "reasoning": f"nearest template: {texts[top1_index]}",
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data/eval/dialogpilot-500-v1/cases.jsonl",
    )
    parser.add_argument(
        "--reviewed-candidates",
        type=Path,
        help="fixed 100-case independently reviewed synthetic JSONL",
    )
    parser.add_argument(
        "--external-bundle",
        type=Path,
        help="versioned DatasetBundle used for source capture only",
    )
    parser.add_argument(
        "--external-split",
        choices=("dev", "heldout"),
        default="dev",
    )
    parser.add_argument(
        "--expected-sha256",
        help="fail before inference if the reviewed candidate file hash differs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts/eval/intent-fusion-mini-2026-08-31",
    )
    parser.add_argument("--semantic-model", default="BAAI/bge-m3")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--embedding-batch-size", type=int, default=16)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument(
        "--skip-semantic",
        action="store_true",
        help="capture LLM/ngram/pattern only; a separate compatible runtime may add semantic outputs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv(ROOT / ".env")
    candidate_sha256 = None
    if args.reviewed_candidates and args.external_bundle:
        raise RuntimeError("choose either --reviewed-candidates or --external-bundle")
    if args.reviewed_candidates:
        candidate_sha256 = hashlib.sha256(args.reviewed_candidates.read_bytes()).hexdigest()
        if args.expected_sha256 and candidate_sha256 != args.expected_sha256:
            raise RuntimeError(
                f"candidate SHA-256 mismatch: expected {args.expected_sha256}, got {candidate_sha256}"
            )
        cases = load_reviewed_candidate_cases(args.reviewed_candidates)
    elif args.external_bundle:
        cases = load_external_intent_cases(args.external_bundle, split=args.external_split)
    else:
        cases = build_cases(args.dataset)
    cases_path = args.output_dir / "cases.jsonl"
    outputs_path = args.output_dir / "source-outputs.jsonl"
    report_path = args.output_dir / "report.json"
    _write_jsonl(cases_path, cases)
    _write_json(
        args.output_dir / "templates.json",
        {
            category.value: templates
            for category, templates in _SEMANTIC_PROTOTYPES.items()
        },
    )
    if args.build_only:
        print(f"built {len(cases)} cases at {cases_path}")
        return

    existing: dict[str, dict[str, Any]] = {}
    if outputs_path.exists():
        existing = {
            row["case_id"]: row
            for row in (
                json.loads(line) for line in outputs_path.read_text(encoding="utf-8").splitlines() if line
            )
        }
    outputs = asyncio.run(_capture_llm_and_local(
        cases, existing, concurrency=max(1, args.concurrency)
    ))
    _write_jsonl(outputs_path, [outputs[case["case_id"]] for case in cases])
    if args.skip_semantic:
        print(f"wrote non-semantic sources to {outputs_path}")
        return
    _capture_semantic(
        cases,
        outputs,
        model_name=args.semantic_model,
        batch_size=max(1, args.embedding_batch_size),
    )
    _write_jsonl(outputs_path, [outputs[case["case_id"]] for case in cases])
    if args.reviewed_candidates:
        assert candidate_sha256 is not None
        report = sealed_validation_report(
            cases,
            outputs,
            semantic_model=args.semantic_model,
            candidate_sha256=candidate_sha256,
        )
    elif args.external_bundle:
        report = {
            "schema_version": 1,
            "status": "source_capture_only_no_parameter_selection",
            "case_count": len(cases),
            "analysis_split": args.external_split,
            "semantic_model": args.semantic_model,
        }
    else:
        report = summary_report(cases, outputs, semantic_model=args.semantic_model)
    _write_json(report_path, report)
    print(f"wrote report to {report_path}")


if __name__ == "__main__":
    main()
