"""Materialize reviewed dialogue prefixes and family-balanced offline indices."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import random

from transformers import AutoTokenizer

from application.encoder_input import EncoderInput
from evaluation.domain_dialogue_teacher import AuthoredBatch, ReviewedBatch, accepted_rows
from evaluation.semantic_encoder_experiment import BACKBONES
from infrastructure.target_domain_encoder import render_domain_input


def family_indices(rows, seed=17):
    """Equal family mass within each label, retaining original class counts.

    This is dataset resampling, not another Trainer or a production decision rule.
    """
    rng = random.Random(seed)
    families = defaultdict(lambda: defaultdict(list))
    for index, row in enumerate(rows):
        families[row.label][row.group_id].append(index)
    indices = []
    for label in sorted(families):
        groups = families[label]
        names = sorted(groups)
        count = sum(map(len, groups.values()))
        chosen = (names * (count // len(names))) + rng.sample(names, count % len(names))
        rng.shuffle(chosen)
        indices.extend(rng.choice(groups[name]) for name in chosen)
    rng.shuffle(indices)
    return indices


def validate_batch(record, language, index):
    """Validate provenance at the raw-record to dataset boundary."""
    split = "train" if index < 16 else "calibration" if index < 20 else "heldout"
    if (record["batch_id"], record["language"], record["split"]) != (
        f"domain-v3:{language}:{index}", language, split
    ):
        raise ValueError("batch provenance mismatch")
    if record["status"] not in {"reviewed", "failed"}:
        raise ValueError("unknown batch outcome")


def build(source: Path, output: Path):
    if output.exists():
        raise ValueError("dataset destination already exists")
    manifest = {"scope": "Synthetic author/blind-review agreement, not independent human gold",
                "sources": {}, "splits": {}, "rejections": []}
    seen, owners = set(), {}
    pending = {}
    for language in ("zh", "en"):
        name, revision = BACKBONES[language]
        tokenizer = AutoTokenizer.from_pretrained(name, revision=revision, local_files_only=True)
        additions = defaultdict(list)
        paths = sorted((source / language).glob("batch-*.json"))
        if {p.name for p in paths} != {f"batch-{i:02}.json" for i in range(24)}:
            raise ValueError("registered batch set incomplete")
        for path in paths:
            record = json.loads(path.read_text())
            validate_batch(record, language, int(path.stem.split("-")[1]))
            manifest["sources"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            if record["status"] != "reviewed":
                manifest["rejections"].append({"source": str(path), "reason": "failed_batch"})
                continue
            rows, rejected = accepted_rows(AuthoredBatch.model_validate(record["authored"]),
                ReviewedBatch.model_validate(record["reviewed"]), record["batch_id"], language)
            manifest["rejections"].extend(rejected)
            for row in rows:
                if len(tokenizer(render_domain_input(EncoderInput.from_record(row)))["input_ids"]) > 256:
                    manifest["rejections"].append({"case_id": row["case_id"], "reason": "token_budget"})
                else:
                    additions[record["split"]].append(row)
        for split in ("train", "calibration", "heldout"):
            base = Path(f"data/training/domain-encoder-v1/{language}/{split}.jsonl")
            rows = [json.loads(line) for line in base.read_text().splitlines()]
            manifest["sources"][str(base)] = hashlib.sha256(base.read_bytes()).hexdigest()
            rows += additions[split]
            for row in rows:
                identity = (language, EncoderInput.from_record(row).identity())
                if identity in seen or owners.setdefault(row["group_id"], split) != split:
                    raise ValueError("duplicate input or family crossing splits")
                seen.add(identity)
            key = f"{language}/{split}.jsonl"
            pending[key] = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
            manifest["splits"][key] = {"rows": len(rows), "new_rows": len(additions[split]),
                "new_labels": dict(Counter(r["label"] for r in additions[split])),
                "new_families": len({r["group_id"] for r in additions[split]}),
                "sha256": hashlib.sha256(pending[key].encode()).hexdigest()}
    for relative, text in pending.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["splits"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    build(**vars(parser.parse_args()))
