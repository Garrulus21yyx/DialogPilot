"""Reproducible language-specific development data; no generated runtime rules."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from application.encoder_input import EncoderInput


BITEXT_REVISION = "430d1a89bd93bd1fa23c16f29dd53e73f0087443"
BITEXT_SOURCE = "https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset"
SPLITS = ("train", "calibration", "heldout")
LABELS = ("general_qa", "product_identification", "refund_status_summary", "__DEFER__")


def _identity(text):
    return re.sub(r"[^\w]+", "", text.casefold())


def _split(group):
    bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
    return "train" if bucket < 70 else "calibration" if bucket < 85 else "heldout"


def build_data(*, root: Path, seeds: Path, bitext: Path, output: Path):
    authored = json.loads(seeds.read_text())
    rows = {lang: {split: [] for split in SPLITS} for lang in ("zh", "en")}
    seen = {lang: set() for lang in rows}

    def add(language, split, value):
        identity = EncoderInput.from_record(value).identity()
        if identity in seen[language]:
            return
        seen[language].add(identity)
        rows[language][split].append({**value, "language": language})

    # Old Chinese data remains regression/training material, not fresh gold.
    for split in SPLITS:
        for line in (root / "data/eval/target-encoder-zh-v1" / f"{split}.jsonl").read_text().splitlines():
            original = json.loads(line)
            add("zh", split, {**original, "case_id": "legacy:" + original["case_id"],
                              "group_id": "legacy:" + _identity(original["text"]),
                              "source": "existing-synthetic-zh-v1"})

    # Restrict mappings to compatible capabilities; order-specific delivery
    # queries are NOT relabelled as knowledge QA. All other tasks defer.
    counts = {}
    with bitext.open(newline="") as source:
        external = sorted(csv.DictReader(source), key=lambda row: hashlib.sha256(row["instruction"].encode()).hexdigest())
    for row in external:
        original_label = row["intent"]
        limit = 900 if original_label in {"track_refund", "check_refund_policy", "check_payment_methods", "get_refund"} else 80
        if counts.get(original_label, 0) >= limit:
            continue
        counts[original_label] = counts.get(original_label, 0) + 1
        # Original entity placeholders carry no label/answer information.
        text = re.sub(r"\{\{[^}]+\}\}", "provided value", row["instruction"])
        group = "bitext:" + _identity(text)
        label = ("refund_status_summary" if original_label == "track_refund" else
                 "general_qa" if original_label in {"check_refund_policy", "check_payment_methods"} else "__DEFER__")
        add("en", _split(group), dict(case_id=hashlib.sha256(text.encode()).hexdigest()[:20],
            text=text, label=label, group_id=group, source="bitext:" + BITEXT_REVISION,
            source_intent=original_label, license="CDLA-Sharing-1.0"))

    for language, groups in authored.items():
        for label, phrases in groups["phrases"].items():
            for index, phrase in enumerate(phrases):
                group = f"authored:{language}:{label}:{index}"
                split = _split(group)
                for variation, prefix in enumerate(groups["prefixes"]):
                    add(language, split, dict(case_id=f"{group}:{variation}", text=prefix + phrase,
                        label=label, group_id=group, source="agent-authored-synthetic"))
        # READ and WRITE confirmations share response vocabulary, not a lexical
        # shortcut. Each prompt family and all its reply variants stay together.
        for kind, prompts in groups["prompts"].items():
            for index, prompt in enumerate(prompts):
                group = f"dialogue:{language}:{kind}:{index}"
                split = _split(group)
                for variation, answer in enumerate(groups["answers"]):
                    add(language, split, dict(case_id=f"{group}:{variation}", text=answer,
                        messages=[{"role": "assistant", "content": prompt}], group_id=group,
                        label="refund_status_summary" if kind == "read" else "__DEFER__",
                        source="agent-authored-synthetic-dialogue"))
                for variation, answer in enumerate(groups["declines"]):
                    add(language, split, dict(case_id=f"{group}:decline:{variation}", text=answer,
                        messages=[{"role": "assistant", "content": prompt}], group_id=group,
                        label="__DEFER__", source="agent-authored-synthetic-dialogue"))
        # Self-contained questions amid irrelevant conversation; label remains
        # the current request. Histories are not evidence or authorization.
        for label, phrases in groups["phrases"].items():
            for index, phrase in enumerate(phrases):
                group = f"authored:{language}:{label}:{index}"
                add(language, _split(group), dict(case_id=group+":history", text=phrase,
                    messages=[{"role": "assistant", "content": groups["irrelevant"]}],
                    label=label, group_id=group, source="agent-authored-synthetic-dialogue"))

    if output.exists():
        raise ValueError("dataset output already exists")
    output.mkdir(parents=True)
    manifest = {"source_revision": BITEXT_REVISION, "source_url": BITEXT_SOURCE,
                "source_sha256": hashlib.sha256(bitext.read_bytes()).hexdigest(),
                "seeds_sha256": hashlib.sha256(seeds.read_bytes()).hexdigest(),
                "limitations": ["Synthetic/templated data, not real customer gold", "Legacy zh heldout already consumed",
                                "Bitext seed families unavailable; exact-normalized grouping only"], "splits": {}}
    for language, splits in rows.items():
        (output / language).mkdir()
        manifest["splits"][language] = {}
        for split, values in splits.items():
            path = output / language / f"{split}.jsonl"
            path.write_text("".join(json.dumps(value, ensure_ascii=False) + "\n" for value in values))
            manifest["splits"][language][split] = {"count": len(values), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bitext", type=Path, required=True)
    parser.add_argument("--seeds", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_data(root=Path(__file__).resolve().parents[1], **vars(args)), indent=2))
