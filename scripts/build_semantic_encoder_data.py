"""Extend existing data with whole counterfactual dialogue families (synthetic)."""
import argparse
import hashlib
import json
from pathlib import Path

from application.encoder_input import EncoderInput
from evaluation.encoder_language_data import _split, SPLITS


def build(source: Path, families: Path, output: Path, boundaries: Path | None = None):
    if output.exists():
        raise ValueError("dataset output exists")
    seeds = json.loads(families.read_text())
    result = {}
    for language in ("zh", "en"):
        rows = {s: [json.loads(line) for line in (source / language / f"{s}.jsonl").read_text().splitlines()]
                for s in SPLITS}
        seen = {EncoderInput.from_record(r).identity() for split in rows.values() for r in split}
        group = seeds[language]
        for family, prompts in enumerate(group["families"]):
            # Bilingual counterpart families also share split; do not derive
            # split from label, answer, or language.
            family_id = f"semantic-family:{family}"
            split = _split(family_id)
            for kind, prompt in zip(("refund_status_summary", "product_identification", "__DEFER__"), prompts):
                for positive, answers in ((True, group["yes"]), (False, group["no"])):
                    for answer in answers:
                        for history in ([], group["irrelevant"]):
                            row = dict(text=answer, language=language,
                                messages=history + [{"role": "assistant", "content": prompt}],
                                label=kind if positive else "__DEFER__", group_id=family_id,
                                source="agent-authored-counterfactual-dialogue-v1")
                            identity = EncoderInput.from_record(row).identity()
                            if identity in seen:
                                continue
                            seen.add(identity)
                            row["case_id"] = "sem:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
                            rows[split].append(row)
        if boundaries:
            operators = json.loads(boundaries.read_text())[language]
            for split, values in rows.items():
                # Compose full positive utterances with a second goal, not just
                # bare "also cancel" replies. Keep the source family split.
                originals = sorted((r for r in values if r["label"] != "__DEFER__" and not r.get("messages")),
                                   key=lambda r: r["case_id"])
                for index, original in enumerate(originals):
                    operator = operators["secondary"][index % len(operators["secondary"])]
                    for variant, text, label in (
                        ("compound", original["text"] + ("，" if language == "zh" else "; ") + operator, "__DEFER__"),
                        ("read-only", operators["read_only"][index % 2] + original["text"], original["label"]),
                    ):
                        row = {**original, "case_id": original["case_id"] + ":" + variant,
                               "text": text, "label": label, "source": "synthetic-compositional-boundary-v1"}
                        identity = EncoderInput.from_record(row).identity()
                        if identity not in seen:
                            seen.add(identity)
                            values.append(row)
                # Contextual compounds retain the positive question's language
                # as well as the independent second goal.
                contextual = [r for r in list(values) if r.get("messages") and r["label"] != "__DEFER__"]
                for index, original in enumerate(contextual):
                    row = {**original, "case_id": original["case_id"] + ":compound",
                           "text": original["text"] + ("，" if language == "zh" else "; ")
                               + operators["secondary"][index % len(operators["secondary"])],
                           "label": "__DEFER__", "source": "synthetic-compositional-boundary-v1"}
                    identity = EncoderInput.from_record(row).identity()
                    if identity not in seen:
                        seen.add(identity)
                        values.append(row)
            for index, text in enumerate(operators["business"]):
                group_id = f"semantic-business:{index}"
                for variant, prefix in enumerate(["", "麻烦你，", "请帮忙，"] if language == "zh" else ["", "Hi, ", "Please help: "]):
                    row = dict(case_id=f"{language}:{group_id}:{variant}", group_id=group_id,
                               language=language, text=prefix+text, label="__DEFER__",
                               source="synthetic-authority-boundary-v1")
                    identity = EncoderInput.from_record(row).identity()
                    if identity not in seen:
                        seen.add(identity)
                        rows[_split(group_id)].append(row)
        result[language] = rows
    output.mkdir(parents=True)
    manifest = dict(source=str(source), families_sha256=hashlib.sha256(families.read_bytes()).hexdigest(),
                    scope="Synthetic counterfactual families; old development splits remain consumed", splits={})
    if boundaries:
        manifest["boundaries_sha256"] = hashlib.sha256(boundaries.read_bytes()).hexdigest()
    for language, splits in result.items():
        (output / language).mkdir()
        manifest["splits"][language] = {}
        for split, rows in splits.items():
            path = output / language / f"{split}.jsonl"
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
            manifest["splits"][language][split] = dict(count=len(rows), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=Path("data/training/encoder-language-v3"))
    p.add_argument("--families", type=Path, default=Path("data/training/semantic-context-families-v1.json"))
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--boundaries", type=Path)
    build(**vars(p.parse_args()))
