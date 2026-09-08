"""Development diagnosis of learned components, separate from executable admission."""
import argparse
import json
from pathlib import Path
import statistics
import time

import torch
from sklearn.metrics import precision_recall_fscore_support

from application.encoder_input import EncoderInput
from application.target_encoder_artifact import DEFER_LABEL
from evaluation.encoder_components import CAPABILITIES, COMPONENTS, component_routing_scores
from evaluation.semantic_encoder_experiment import SemanticScorer, digest, render


def evaluate(model: Path, data: Path, output: Path):
    if output.exists():
        raise ValueError("diagnostic already exists")
    torch.set_num_threads(2)
    scorer = SemanticScorer(model, device="cuda")
    classes = (DEFER_LABEL, *CAPABILITIES)
    rows = list(map(json.loads, (data / "heldout.jsonl").read_text().splitlines()))
    details, times = [], []
    for row in rows:
        encoded = scorer.tokenizer(render(EncoderInput.from_record(row)), return_tensors="pt", truncation=False)
        if encoded["input_ids"].shape[1] > scorer.max_tokens:
            raise ValueError("development overflow")
        encoded = {k: v.to("cuda") for k, v in encoded.items()}
        started = time.perf_counter()
        with torch.inference_mode():
            values = scorer.model(**encoded).logits.sigmoid().cpu().numpy()[0]
        times.append((time.perf_counter() - started) * 1000)
        scores = component_routing_scores([values], COMPONENTS, classes)[0]
        details.append({"case_id": row["case_id"], "expected": row["label"],
            "annotation_basis": row["annotation_basis"], "targets": row["semantic_targets"],
            "scores": dict(zip(COMPONENTS, map(float, values))),
            "uncalibrated_projection": classes[int(scores.argmax())]})
    metrics = {}
    for name in COMPONENTS:
        labeled = [r for r in details if r["targets"][name] is not None]
        gold = [r["targets"][name] for r in labeled]
        predicted = [int(r["scores"][name] >= .5) for r in labeled]
        precision, recall, f1, _ = precision_recall_fscore_support(
            gold, predicted, average="binary", zero_division=0)
        metrics[name] = {"labeled": len(labeled), "correct": sum(
            a == b for a, b in zip(gold, predicted)), "positive_support": sum(gold),
            "precision": float(precision), "recall": float(recall), "f1": float(f1)}
    training = list(map(json.loads, (data / "train.jsonl").read_text().splitlines()))
    support = {}
    for name in CAPABILITIES:
        active = [r for r in training if r["semantic_targets"][f"requested:{name}"] == 1]
        support[name] = {str(value): sum(r["semantic_targets"][f"denied:{name}"] == value for r in active)
                         for value in (None, 0, 1)}
    report = {"manifest_sha256": digest(model / "manifest.json"),
        "data_sha256": digest(data / "heldout.jsonl"), "status": scorer.raw_manifest["status"],
        "scope": "Raw development diagnostic; no executable admission or fresh evaluation",
        "device": "cuda", "model_forward_median_ms": statistics.median(times[1:]),
        "components": metrics, "training_polarity_support": support, "details": details}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "details"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    evaluate(**vars(parser.parse_args()))
