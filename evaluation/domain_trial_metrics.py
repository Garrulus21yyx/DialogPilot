"""Offline raw classification of frozen candidates, including rejected models.

No acceptance thresholds are changed. This is not an online encoder adapter.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from sklearn.metrics import accuracy_score, f1_score

from application.encoder_input import EncoderInput
from infrastructure.target_domain_encoder import render_domain_input, validate_model_inputs


def summarize(details, labels):
    expected = [row["expected"] for row in details]
    predicted = [row["predicted"] for row in details]
    return {"total": len(details), "correct": sum(a == b for a, b in zip(expected, predicted)),
            "accuracy": accuracy_score(expected, predicted),
            "macro_f1": f1_score(expected, predicted, labels=labels, average="macro", zero_division=0),
            "predicted_counts": dict(Counter(predicted))}


def run(model_path, data, output):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if output.exists():
        raise ValueError("trial metrics output exists")
    manifest = json.loads((model_path / "manifest.json").read_text())
    validate_model_inputs(model_path, manifest)
    if hashlib.sha256((model_path / "model.safetensors").read_bytes()).hexdigest() != manifest["model_sha256"]:
        raise ValueError("trial weights changed")
    torch.set_num_threads(2)
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True).float().cuda().eval()
    labels = [model.config.id2label[i] for i in range(model.config.num_labels)]
    reports = {}
    for path in data:
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        details = []
        for row in rows:
            inputs = tokenizer(render_domain_input(EncoderInput.from_record(row)), return_tensors="pt", truncation=False)
            if inputs["input_ids"].shape[1] > manifest["max_tokens"]:
                raise ValueError(f"trial input overflow: {row['case_id']}")
            with torch.inference_mode():
                probabilities = model(**{k: v.cuda() for k, v in inputs.items()}).logits.softmax(-1)[0].cpu().tolist()
            details.append({"case_id": row["case_id"], "expected": row["label"],
                            "predicted": labels[max(range(len(labels)), key=probabilities.__getitem__)],
                            "probabilities": probabilities})
        reports[str(path)] = {"source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                              "summary": summarize(details, labels), "details": details}
    result = {"scope": "Frozen candidate raw argmax classification; no threshold selection or production enablement",
              "model_manifest_sha256": hashlib.sha256((model_path / "manifest.json").read_bytes()).hexdigest(),
              "classes": labels, "reports": reports}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({path: value["summary"] for path, value in reports.items()}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--data", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(**vars(parser.parse_args()))
