"""Train one contextual domain classifier per language using standard Trainer."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import torch
from datasets import Dataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer, DataCollatorWithPadding, Trainer, TrainingArguments, set_seed

from application.domain_encoder import DOMAINS, DEFER_DOMAIN, DOMAIN_MINIMUM_MARGIN, DomainEncoderManifest
from infrastructure.target_domain_encoder import render_domain_input, model_input_digests
from evaluation.semantic_encoder_experiment import BACKBONES
from evaluation.target_encoder_training import _load, _select_threshold, _evaluate_threshold


def fit(data: Path, output: Path, language: str):
    if output.exists():
        raise ValueError("domain training output already exists")
    if not torch.cuda.is_available():
        raise RuntimeError("this registered training run requires CUDA")
    set_seed(17)
    torch.set_num_threads(2)
    splits = {s: _load(data / f"{s}.jsonl") for s in ("train", "calibration", "heldout")}
    seen, groups = set(), {}
    classes = (DEFER_DOMAIN, *DOMAINS)
    for split, rows in splits.items():
        for row in rows:
            if row.label not in classes or row.language != language:
                raise ValueError("domain dataset label/language mismatch")
            if row.input.identity() in seen or groups.setdefault(row.group_id, split) != split:
                raise ValueError("domain data leakage")
            seen.add(row.input.identity())
    name, revision = BACKBONES[language]
    tokenizer = AutoTokenizer.from_pretrained(name, revision=revision, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(name, revision=revision,
        local_files_only=True, num_labels=len(classes), id2label=dict(enumerate(classes)),
        label2id={label: i for i, label in enumerate(classes)})
    encoded = []
    for row in splits["train"]:
        values = tokenizer(render_domain_input(row.input), truncation=False)
        if len(values["input_ids"]) > 256:
            raise ValueError("training input exceeds encoder budget")
        encoded.append({**values, "labels": classes.index(row.label)})
    args = TrainingArguments(output_dir=str(output / "trainer"), num_train_epochs=4,
        learning_rate=3e-5, per_device_train_batch_size=24, weight_decay=.01, warmup_ratio=.1,
        save_strategy="no", report_to=[], seed=17, data_seed=17, fp16=True,
        logging_steps=200, disable_tqdm=True)
    trainer = Trainer(model=model, args=args, train_dataset=Dataset.from_list(encoded),
                      data_collator=DataCollatorWithPadding(tokenizer))
    started = time.perf_counter()
    trained = trainer.train()
    model.half().save_pretrained(output, safe_serialization=True)
    tokenizer.save_pretrained(output)
    model = AutoModelForSequenceClassification.from_pretrained(output, local_files_only=True).float().to("cuda").eval()
    scores = {}
    for split in ("calibration", "heldout"):
        values = []
        for row in splits[split]:
            inputs = tokenizer(render_domain_input(row.input), return_tensors="pt", truncation=False)
            if inputs["input_ids"].shape[1] > 256:
                values.append([1.] + [0.] * len(DOMAINS))
                continue
            with torch.inference_mode():
                probabilities = model(**{k: v.to("cuda") for k, v in inputs.items()}).logits.softmax(-1)[0].cpu().numpy()
            # Same margin eligibility as the online domain adapter, before calibration.
            ordered = np.sort(probabilities)
            if ordered[-1] - ordered[-2] < DOMAIN_MINIMUM_MARGIN:
                probabilities = np.array([1.] + [0.] * len(DOMAINS))
            values.append(probabilities)
        scores[split] = np.asarray(values)
    calibration, development, thresholds = {}, {}, {}
    for domain in DOMAINS:
        c = _select_threshold(domain, classes, scores["calibration"],
            tuple(r.label for r in splits["calibration"]), .88, minimum_empirical_precision=.98)
        d = _evaluate_threshold(domain, classes, scores["heldout"],
            tuple(r.label for r in splits["heldout"]), c["threshold"])
        calibration[domain], development[domain] = c, d
        if c["accepted"] >= 10 and d["accepted"] >= 10 and d["correct"] / d["accepted"] >= .98:
            thresholds[domain] = c["threshold"]
    manifest = {**asdict(DomainEncoderManifest(language, thresholds,
        hashlib.sha256((output / "model.safetensors").read_bytes()).hexdigest(),
        status="CANDIDATE" if thresholds else "REJECTED")),
        "calibration": calibration, "development": development, "max_tokens": 256,
        "input_files_sha256": model_input_digests(output),
        "backbone": {"name": name, "revision": revision}, "calibration_device": "cuda",
        "datasets": {s: hashlib.sha256((data / f"{s}.jsonl").read_bytes()).hexdigest() for s in splits}}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "training.json").write_text(json.dumps({"metrics": trained.metrics, "config": args.to_dict(),
        "elapsed_seconds": time.perf_counter() - started}, default=str, indent=2) + "\n")
    print(json.dumps({"status": manifest["status"], "thresholds": thresholds, "development": development}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    fit(**vars(parser.parse_args()))
