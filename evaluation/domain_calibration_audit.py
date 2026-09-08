"""Explain a frozen domain model's calibration rejection without changing it."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from application.domain_encoder import DOMAINS, DEFER_DOMAIN, DOMAIN_MINIMUM_MARGIN
from application.encoder_input import EncoderInput
from evaluation.target_encoder_training import _evaluate_threshold, _select_threshold
from infrastructure.target_domain_encoder import TargetDomainEncoder


def analyze(scores, expected):
    classes = (DEFER_DOMAIN, *DOMAINS)
    raw = np.asarray(scores)
    masked = raw.copy()
    margins = np.sort(raw, axis=1)[:, -1] - np.sort(raw, axis=1)[:, -2]
    masked[margins < DOMAIN_MINIMUM_MARGIN] = [1.] + [0.] * len(DOMAINS)
    summary = {}
    for index, domain in enumerate(classes[1:], 1):
        before = raw.argmax(axis=1) == index
        after = masked.argmax(axis=1) == index
        curve = [_evaluate_threshold(domain, classes, masked, expected, threshold)
                 for threshold in sorted(set(float(r[index]) for r in masked[after]))]
        empirical = [point for point in curve if point["accepted"] >= 10 and point["correct"] / point["accepted"] >= .98]
        selected = _select_threshold(domain, classes, masked, expected, .88, minimum_empirical_precision=.98)
        summary[domain] = {"positive_rows": sum(label == domain for label in expected),
            "argmax_rows": int(before.sum()), "margin_rows": int(after.sum()),
            "margin_correct": sum(label == domain for label, eligible in zip(expected, after) if eligible),
            "empirical_only_candidate": max(empirical, key=lambda p: p["accepted"], default=None),
            "selected": selected, "curve": curve}
        if not before.any():
            reason = "NO_DOMAIN_WINNER"
        elif not after.any():
            reason = "MARGIN_REJECTED_ALL"
        elif not empirical:
            reason = "NO_98_PERCENT_CANDIDATE_WITH_10_SAMPLES"
        elif not selected["enabled"]:
            reason = "CONFIDENCE_BOUND_SAMPLE_SUPPORT_INSUFFICIENT"
        else:
            reason = "CALIBRATION_CANDIDATE_AVAILABLE"
        summary[domain]["diagnosis"] = reason
    return summary


def run(data, model, output, device):
    if output.exists():
        raise ValueError("audit output exists")
    rows = [json.loads(s) for s in data.read_text().splitlines()]
    encoder = TargetDomainEncoder(model, device=device, evaluation=True)
    scores, details = [], []
    for row in rows:
        prediction = encoder.predict(EncoderInput.from_record(row))
        by_domain = {p.candidate_id: p.score for p in prediction.candidates}
        vector = [prediction.defer_score] + [by_domain[d] for d in DOMAINS]
        scores.append(vector)
        details.append({"case_id": row["case_id"], "group_id": row["group_id"], "expected": row["label"],
                        "scores": vector, "relation": row.get("relation", "v1")})
    summary = analyze(scores, [r["label"] for r in rows])
    for domain, result in summary.items():
        positives = [row for row in rows if row["label"] == domain]
        result["positive_groups"] = len({r["group_id"] for r in positives})
        result["positive_relations"] = dict(Counter(r.get("relation", "v1") for r in positives))
    report = {"scope": "Frozen v2 weights; consumed calibration only; no threshold change or release decision",
        "classes": [DEFER_DOMAIN, *DOMAINS], "data_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
        "model_manifest_sha256": hashlib.sha256((model / "manifest.json").read_bytes()).hexdigest(),
        "device": device, "summary": summary, "details": details}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({d: {k: v for k, v in s.items() if k != "curve"} for d, s in summary.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    run(**vars(parser.parse_args()))
