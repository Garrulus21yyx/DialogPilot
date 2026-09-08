"""Source-reviewed domain labels; same-domain goals do not imply coordination."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from application.domain_encoder import DOMAINS, DEFER_DOMAIN
from application.encoder_input import EncoderInput
from evaluation.encoder_language_data import _split


def combine(a, b):
    if a == "general":
        return b
    if b == "general":
        return a
    return a if a == b and a != DEFER_DOMAIN else DEFER_DOMAIN


def source_label(row, annotations):
    if "source_intent" in row:
        return annotations["bitext_intents"][row["source_intent"]]
    parts = row["group_id"].split(":")
    if parts[0] == "legacy":
        return None  # No reviewed business-domain annotation for old task templates.
    language, kind, index = parts[1], parts[2], int(parts[3])
    if parts[0] == "authored":
        return annotations[language]["phrases"][kind][index]
    if parts[0] != "dialogue":
        raise ValueError("unreviewed domain source")
    owner = annotations[language]["prompts"][kind][index]
    if ":decline:" not in row["case_id"]:
        return owner
    answer = int(row["case_id"].rsplit(":", 1)[1])
    if answer in (5, 6):
        return combine(owner, "order_logistics")
    if answer == 7:
        return "billing_refund"  # Explicitly replaces the proposed task with a refund request.
    if answer == 9:
        return owner if owner in {"billing_refund", "order_logistics"} else DEFER_DOMAIN
    return DEFER_DOMAIN


def build(output: Path):
    if output.exists():
        raise ValueError("domain dataset output already exists")
    root = Path("data/training")
    annotation_path = root / "domain-encoder-annotations-v1.json"
    annotations = json.loads(annotation_path.read_text())
    families_path = root / "semantic-context-families-v1.json"
    families = json.loads(families_path.read_text())
    manifest = {"schema": "domain-training-v1", "annotation_sha256": hashlib.sha256(annotation_path.read_bytes()).hexdigest(),
                "families_sha256": hashlib.sha256(families_path.read_bytes()).hexdigest(),
                "scope": "Synthetic and Bitext-derived development, not fresh customer gold", "sources": {}, "splits": {}}
    for language in ("zh", "en"):
        splits = {}
        for split in ("train", "calibration", "heldout"):
            source = root / "encoder-language-v3" / language / f"{split}.jsonl"
            manifest["sources"][str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
            rows = []
            for row in map(json.loads, source.read_text().splitlines()):
                label = source_label(row, annotations)
                if label is not None:
                    rows.append({**row, "label": label, "annotation_basis": "source-domain-review"})
            splits[split] = rows
        for family, prompts in enumerate(families[language]["families"]):
            group = f"semantic-family:{family}"
            for owner, prompt in zip(("billing_refund", "product_technical", annotations[language]["semantic_write"][family]), prompts):
                for positive, answers in ((True, families[language]["yes"]), (False, families[language]["no"])):
                    for index, answer in enumerate(answers):
                        label = owner if positive else DEFER_DOMAIN
                        if not positive:
                            if index in (3, 7):
                                label = combine(owner, "order_logistics")
                            elif index == 2 and owner in {"billing_refund", "order_logistics"}:
                                label = owner
                            elif index == 4 and owner in {"billing_refund", "order_logistics"}:
                                label = owner
                        row = dict(text=answer, messages=[{"role": "assistant", "content": prompt}],
                            language=language, label=label, group_id=group, source="reviewed-domain-dialogue",
                            annotation_basis="source-domain-review")
                        row["case_id"] = "domain:" + hashlib.sha256(EncoderInput.from_record(row).identity().encode()).hexdigest()[:24]
                        splits[_split(group)].append(row)
        seen, owners = {}, {}
        for split, rows in splits.items():
            # Only simple authored originals form synthetic compounds. No semantic
            # splitting at inference, and no broad external utterance is assumed atomic.
            pool = sorted((r for r in rows if r.get("source") == "agent-authored-synthetic"
                           and r["label"] != DEFER_DOMAIN), key=lambda r: r["case_id"])
            by_owner = {k: [r for r in pool if r["label"] == k] for k in DOMAINS}
            for i, row in enumerate(pool):
                for owner in DOMAINS:
                    if not by_owner[owner]:
                        continue
                    other = by_owner[owner][i % len(by_owner[owner])]
                    if row["case_id"] == other["case_id"]:
                        continue
                    text = row["text"] + ("；另外，" if language == "zh" else "; also, ") + other["text"]
                    replacement = (f"不要处理这个请求：{row['text']}。改为处理：{other['text']}" if language == "zh"
                        else f"Do not handle this request: {row['text']} Instead handle: {other['text']}")
                    for kind, message, label in (("compound", text, combine(row["label"], owner)),
                                                 ("replacement", replacement, owner)):
                        rows.append({**row, "case_id": f"{row['case_id']}:{kind}:{other['case_id']}",
                            "text": message, "label": label, "source": "domain-composition-v1",
                            "source_case_ids": [row["case_id"], other["case_id"]], "annotation_basis": kind})
            unique = []
            for row in rows:
                if row["label"] not in {*DOMAINS, DEFER_DOMAIN}:
                    raise ValueError("unknown domain annotation")
                if owners.setdefault(row["group_id"], split) != split:
                    raise ValueError("domain family leaked")
                identity = EncoderInput.from_record(row).identity()
                if identity in seen:
                    if seen[identity] != row["label"]:
                        raise ValueError("identical domain inputs have conflicting labels")
                    continue
                seen[identity] = row["label"]
                unique.append(row)
            path = output / language / f"{split}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in unique))
            manifest["splits"][f"{language}/{split}"] = {"count": len(unique),
                "labels": dict(Counter(r["label"] for r in unique)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["splits"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build(**vars(parser.parse_args()))
