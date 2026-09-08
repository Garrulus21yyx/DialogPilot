"""Bounded Transformers classifier experiment; existing policy/calibration stay authoritative."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
import torch
from datasets import Dataset
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          DataCollatorWithPadding, Trainer, TrainingArguments, set_seed)

from application.encoder_input import CONTEXT_INPUT_SCHEMA, EncoderInput
from application.encoder_fast_path import RankedCandidate
from application.target_encoder_artifact import (DEFER_LABEL, TARGET_ENCODER_SCHEMA,
    TargetEncoderClass, TargetEncoderManifest)
from evaluation.target_encoder_training import (TARGETS, _load, _select_threshold,
                                               _evaluate_threshold)


BACKBONES = {
    "zh": ("BAAI/bge-small-zh-v1.5", "7999e1d3359715c523056ef9478215996d62a620"),
    "en": ("BAAI/bge-small-en", "2275a7bdee235e9b4f01fa73aa60d3311983cfea"),
}
MAX_TOKENS = 256


def render(value: EncoderInput) -> str:
    # The whole role-ordered exchange is jointly encoded; no independent
    # history embeddings or inference-time rewriting model.
    return json.dumps({"history": list(value.messages), "objectives": value.objectives,
                       "current_user": value.text}, ensure_ascii=False, separators=(",", ":"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calibrate(classes, probabilities, splits, language, output, data):
    records, language_reports, unsafe_reports = [], {}, {}
    for label, (owner, kind, capability, arguments) in TARGETS.items():
        selected = _select_threshold(label, classes, probabilities["calibration"],
            tuple(item.label for item in splits["calibration"]), .88,
            minimum_empirical_precision=.98)
        heldout = _evaluate_threshold(label, classes, probabilities["heldout"],
            tuple(item.label for item in splits["heldout"]), selected["threshold"])
        class_index = classes.index(label)
        unsafe = sum(item.label == DEFER_LABEL
            and int(probabilities["heldout"][i].argmax()) == class_index
            and float(probabilities["heldout"][i, class_index]) >= selected["threshold"]
            for i, item in enumerate(splits["heldout"]))
        unsafe_reports[label] = unsafe
        enabled = bool(selected["enabled"] and heldout["accepted"] >= 10
                       and heldout["correct"] / max(1, heldout["accepted"]) >= .98 and not unsafe)
        records.append(TargetEncoderClass(label, owner, kind, capability, arguments,
            selected["threshold"], enabled, selected["accepted"], selected["correct"],
            selected["precision_lower_bound"], heldout["accepted"], heldout["correct"],
            heldout["precision_lower_bound"]))
        language_reports[label] = {language: heldout}
    manifest = dict(schema_version=TARGET_ENCODER_SCHEMA,
        status="ACTIVE" if any(r.enabled for r in records) else "REJECTED",
        artifact_version=output.name, bundle_version="customer-service-v1",
        model_filename="model.safetensors", model_sha256=digest(output / "model.safetensors"),
        target_precision=.88, heldout_target_precision=.98, heldout_min_accepts=10,
        classes=[asdict(r) for r in records], required_languages=[language],
        language_reports=language_reports, datasets=[dict(split=s, sha256=digest(data / f"{s}.jsonl"))
            for s in splits], model_type="transformers-sequence-classification",
        input_schema=CONTEXT_INPUT_SCHEMA, max_tokens=MAX_TOKENS,
        calibration_minimum_empirical_precision=.98,
        development_unsafe_accepts=unsafe_reports,
        backbone=dict(zip(("name", "revision"), BACKBONES[language])))
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def train(data: Path, output: Path, language: str, epochs: int = 4):
    if output.exists():
        raise ValueError("experiment output already exists")
    set_seed(17)
    torch.set_num_threads(2)
    splits = {split: _load(data / f"{split}.jsonl") for split in ("train", "calibration", "heldout")}
    seen, groups = set(), {}
    for split, items in splits.items():
        for item in items:
            if item.input.identity() in seen or groups.setdefault(item.group_id, split) != split:
                raise ValueError("input or conversation-family leakage")
            seen.add(item.input.identity())
    classes = tuple(sorted({DEFER_LABEL, *TARGETS}))
    name, revision = BACKBONES[language]
    tokenizer = AutoTokenizer.from_pretrained(name, revision=revision, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(name, revision=revision,
        local_files_only=True, num_labels=len(classes), id2label=dict(enumerate(classes)),
        label2id={label: i for i, label in enumerate(classes)})
    datasets = {}
    for split, items in splits.items():
        rows = []
        for item in items:
            encoded = tokenizer(render(item.input), truncation=False)
            if len(encoded["input_ids"]) > MAX_TOKENS:
                raise ValueError(f"training input exceeds declared budget: {item.case_id}")
            rows.append({**encoded, "labels": classes.index(item.label)})
        datasets[split] = Dataset.from_list(rows)
    output.mkdir(parents=True)
    args = TrainingArguments(output_dir=str(output / "trainer"), num_train_epochs=epochs,
        learning_rate=3e-5, per_device_train_batch_size=24, per_device_eval_batch_size=48,
        weight_decay=.01, warmup_ratio=.1, save_strategy="no", logging_steps=100,
        report_to=[], seed=17, data_seed=17, fp16=torch.cuda.is_available(),
        dataloader_num_workers=0, disable_tqdm=True)
    trainer = Trainer(model=model, args=args, train_dataset=datasets["train"],
                      data_collator=DataCollatorWithPadding(tokenizer))
    started = time.perf_counter()
    result = trainer.train()
    probabilities = {}
    for split in ("calibration", "heldout"):
        logits = trainer.predict(datasets[split]).predictions
        probabilities[split] = torch.softmax(torch.tensor(logits), dim=1).numpy()
    # Standard safetensors serialization; no pickle/custom model runtime.
    model.half().save_pretrained(output, safe_serialization=True)
    tokenizer.save_pretrained(output)
    manifest = calibrate(classes, probabilities, splits, language, output, data)
    (output / "training.json").write_text(json.dumps(dict(metrics=result.metrics,
        elapsed_seconds=time.perf_counter()-started, counts={s: len(v) for s,v in splits.items()},
        versions=dict(torch=torch.__version__, transformers=__import__("transformers").__version__),
        config=args.to_dict()), indent=2, default=str) + "\n")
    print(json.dumps({r["label"]: {k:r[k] for k in ("enabled", "threshold", "heldout_accepted", "heldout_correct")}
                      for r in manifest["classes"]}), flush=True)


class SemanticCandidate:
    """Evaluation-only adapter, no production selection or silent fallback."""
    input_schema = CONTEXT_INPUT_SCHEMA

    def __init__(self, path: Path):
        raw = json.loads((path / "manifest.json").read_text())
        self.manifest = TargetEncoderManifest.from_dict(raw)
        if digest(path / self.manifest.model_filename) != self.manifest.model_sha256:
            raise ValueError("candidate weight digest mismatch")
        self.tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).float().eval()
        self.max_tokens = raw["max_tokens"]

    def validate_registry(self, registry):
        # Reuse the exact existing owner-level registry contract, which only
        # reads self.manifest; no copy of permission or capability logic.
        from application.target_encoder_artifact import TargetTextEncoderArtifact
        TargetTextEncoderArtifact.validate_registry(self, registry)

    def predict(self, value):
        encoded = self.tokenizer(render(value), return_tensors="pt", truncation=False)
        if encoded["input_ids"].shape[1] > self.max_tokens:
            return tuple(RankedCandidate(c.label, 0.) for c in self.manifest.classes), 1.
        with torch.inference_mode():
            probabilities = self.model(**encoded).logits.softmax(-1)[0].tolist()
        scores = {self.model.config.id2label[i]: p for i,p in enumerate(probabilities)}
        return (tuple(RankedCandidate(c.label, scores[c.label]) for c in
                      sorted(self.manifest.classes, key=lambda c: (-scores[c.label], c.label))),
                scores[DEFER_LABEL])


def finalize_candidate(data: Path, trained: Path, output: Path, language: str):
    """Calibrate the deployed CPU/fp32 view of saved fp16 weights, not GPU logits."""
    torch.set_num_threads(2)
    if output.exists():
        raise ValueError("candidate output already exists")
    candidate = SemanticCandidate(trained)
    classes = tuple(candidate.model.config.id2label[i] for i in range(candidate.model.config.num_labels))
    splits = {s: _load(data / f"{s}.jsonl") for s in ("train", "calibration", "heldout")}
    probabilities = {}
    for split in ("calibration", "heldout"):
        scores = []
        for item in splits[split]:
            ranked, defer = candidate.predict(item.input)
            values = {r.candidate_id: r.score for r in ranked}
            values[DEFER_LABEL] = defer
            scores.append([values[c] for c in classes])
        probabilities[split] = np.asarray(scores)
    shutil.copytree(trained, output)
    manifest = calibrate(classes, probabilities, splits, language, output, data)
    print(json.dumps({r["label"]: {k:r[k] for k in ("enabled", "threshold", "heldout_accepted", "heldout_correct")}
                      for r in manifest["classes"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", choices=BACKBONES, required=True)
    parser.add_argument("--epochs", type=int, default=4, choices=range(1, 5))
    parser.add_argument("--finalize-from", type=Path)
    args = vars(parser.parse_args())
    trained = args.pop("finalize_from")
    if trained:
        args.pop("epochs")
        finalize_candidate(trained=trained, **args)
    else:
        train(**args)
