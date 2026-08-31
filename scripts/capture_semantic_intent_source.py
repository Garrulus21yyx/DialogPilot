#!/usr/bin/env python3
"""Add a real SentenceTransformer source to an existing fusion capture file.

This helper deliberately has no DialogPilot imports so it can run in a compatible
embedding environment even when the application virtualenv pins an older tokenizer.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--templates", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--model", default="BAAI/bge-m3")
    parser.add_argument("--batch-size", type=int, default=16)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import numpy as np
    from sentence_transformers import SentenceTransformer

    cases = [
        json.loads(line)
        for line in args.cases.read_text(encoding="utf-8").splitlines()
        if line
    ]
    rows = [
        json.loads(line)
        for line in args.outputs.read_text(encoding="utf-8").splitlines()
        if line
    ]
    outputs = {row["case_id"]: row for row in rows}
    templates_by_label = json.loads(args.templates.read_text(encoding="utf-8"))
    labels: list[str] = []
    texts: list[str] = []
    for label, templates in templates_by_label.items():
        for template in templates:
            labels.append(label)
            texts.append(str(template))

    model = SentenceTransformer(args.model, local_files_only=True)
    template_vectors = model.encode(
        texts,
        batch_size=max(1, args.batch_size),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    message_vectors = model.encode(
        [case["message"] for case in cases],
        batch_size=max(1, args.batch_size),
        normalize_embeddings=True,
        show_progress_bar=True,
    )
    similarities = np.asarray(message_vectors) @ np.asarray(template_vectors).T
    for case, scores in zip(cases, similarities):
        best_by_intent: dict[str, tuple[float, int]] = {}
        for index, (label, score) in enumerate(zip(labels, scores)):
            previous = best_by_intent.get(label)
            if previous is None or float(score) > previous[0]:
                best_by_intent[label] = (float(score), index)
        ranked = sorted(
            best_by_intent.items(), key=lambda item: (-item[1][0], item[0]),
        )
        (top1_label, (top1_score, top1_index)), (top2_label, (top2_score, _)) = ranked[:2]
        outputs[case["case_id"]]["semantic"] = {
            "intent": top1_label,
            "confidence": top1_score,
            "second_intent": top2_label,
            "second_confidence": top2_score,
            "margin": top1_score - top2_score,
            "failed": False,
            "reasoning": f"nearest template: {texts[top1_index]}",
        }
    args.outputs.write_text(
        "".join(
            json.dumps(outputs[case["case_id"]], ensure_ascii=False) + "\n"
            for case in cases
        ),
        encoding="utf-8",
    )
    print(f"added semantic source for {len(cases)} cases using {args.model}")


if __name__ == "__main__":
    main()
