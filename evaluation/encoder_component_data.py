"""Recover only evidenced component annotations; keep opaque legacy labels masked."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from application.encoder_input import EncoderInput
from application.target_encoder_artifact import DEFER_LABEL
from evaluation.encoder_components import CAPABILITIES, COMPONENTS, encode_targets


def known(requested=(), denied=(), planning=0):
    return {**{f"requested:{k}": int(k in requested) for k in CAPABILITIES},
            **{f"denied:{k}": int(k in denied) for k in CAPABILITIES},
            "needs_planning": planning}


def annotate(row, originals, seeds):
    """Data provenance, never inference-time keyword classification."""
    if row["label"] in CAPABILITIES:
        if row["source"] not in {"agent-authored-synthetic", "agent-authored-synthetic-dialogue",
                "existing-synthetic-zh-v1", "agent-authored-counterfactual-dialogue-v1",
                "synthetic-compositional-boundary-v1"}:
            raise ValueError("positive source requires action-level annotation review")
        result = known((row["label"],))
        if row["source"] != "agent-authored-counterfactual-dialogue-v1":
            # Single-action gold does not annotate unrelated negation scope.
            result.update({f"denied:{k}": None for k in CAPABILITIES})
            # Audited, unambiguous affirmative target: only its own polarity is known.
            result[f"denied:{row['label']}"] = 0
        return result, "reviewed-authored-request"
    unknown = {k: None for k in COMPONENTS}
    unknown["needs_planning"] = 1
    if row["source"] == "synthetic-compositional-boundary-v1" and row["case_id"].endswith(":compound"):
        original = originals[row["case_id"].removesuffix(":compound")]
        if original["label"] not in CAPABILITIES:
            raise ValueError("compound primary is not an evidenced positive")
        result, _ = annotate(original, originals, seeds)
        return {**result, "needs_planning": 1}, "recorded-primary-plus-other"
    if row["source"] == "agent-authored-counterfactual-dialogue-v1":
        prompt = row["messages"][-1]["content"]
        kinds = ("refund_status_summary", "product_identification", None)
        mapping = {p: k for family in seeds["families"] for p, k in zip(family, kinds)}
        primary = mapping[prompt]
        # These indices are reviewed source annotations, not language heuristics.
        # 3: "also cancel" does not clearly approve an assistant-only proposal.
        if row["text"] in seeds["no"] and primary:
            response = seeds["no"].index(row["text"])
            if response in (0, 1, 5, 6):
                return known(denied=(primary,), planning=1), "explicit-withdrawal"
            if response == 2:
                # The picture proposal + "another order" is ambiguous.
                if primary == "refund_status_summary":
                    return known((primary,), planning=1), "object-correction"
            if response == 7:
                return known((primary,), planning=1), "affirmed-plus-other"
        # Writes and ambiguous replies do not provide reliable capability absences.
    return unknown, "legacy-components-unknown"


def build(source: Path, output: Path, families: Path):
    if output.exists():
        raise ValueError("component dataset already exists")
    seeds = json.loads(families.read_text())
    manifest = {"source": str(source), "source_hashes": {}, "splits": {},
                "families_sha256": hashlib.sha256(families.read_bytes()).hexdigest(),
                "heldout_known_pair": ["general_qa", "product_identification"],
                "scope": "Synthetic/legacy development; original heldout already consumed",
                "exclusion": "Bitext broad intents and all inherited derivatives lack action-level gold"}
    for language in ("zh", "en"):
        for split in ("train", "calibration", "heldout"):
            path = source / language / f"{split}.jsonl"
            rows = list(map(json.loads, path.read_text().splitlines()))
            manifest["source_hashes"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            # group_id and source_intent survive the old generator's source overwrite.
            excluded = [r for r in rows if r["group_id"].startswith("bitext:") or "source_intent" in r]
            rows = [r for r in rows if not (r["group_id"].startswith("bitext:") or "source_intent" in r)]
            originals = {r["case_id"]: r for r in rows}
            for row in rows:
                row["semantic_targets"], row["annotation_basis"] = annotate(row, originals, seeds[language])
            # Contrast requested vs denied on the same source object. No runtime parser.
            positives = [r for r in rows if r["label"] in CAPABILITIES and not r.get("messages")
                         and r["source"] != "synthetic-compositional-boundary-v1"]
            pools = {k: sorted((r for r in positives if r["label"] == k), key=lambda r: r["case_id"])
                     for k in CAPABILITIES}
            for primary, pool in pools.items():
                for index, row in enumerate(pool):
                    negative = (f"撤回下面这个请求，不要执行：{row['text']}" if language == "zh"
                                else f"Withdraw this request; do not carry it out: {row['text']}")
                    rows.append({**row, "case_id": row["case_id"] + ":withdrawn", "text": negative,
                        "label": DEFER_LABEL, "semantic_targets": known(denied=(primary,), planning=1),
                        "annotation_basis": "explicit-source-withdrawal", "source": "component-composition-v1"})
                    for secondary in CAPABILITIES:
                        if secondary <= primary or not pools[secondary]:
                            continue
                        held_pair = (primary, secondary) == ("general_qa", "product_identification")
                        if held_pair and split != "heldout":
                            continue
                        other = pools[secondary][index % len(pools[secondary])]
                        rows.append({**row, "case_id": row["case_id"] + ":with:" + other["case_id"],
                            "text": row["text"] + ("；另外，" if language == "zh" else "; also, ") + other["text"],
                            "label": DEFER_LABEL, "semantic_targets": known((primary, secondary)),
                            "annotation_basis": "two-affirmed-capabilities", "source": "component-composition-v1",
                            "source_case_ids": [row["case_id"], other["case_id"]]})
            identities, unique = set(), []
            for row in rows:
                encode_targets(row["semantic_targets"])
                identity = EncoderInput.from_record(row).identity()
                if identity not in identities:
                    unique.append(row)
                    identities.add(identity)
            target = output / language / f"{split}.jsonl"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in unique))
            manifest["splits"][f"{language}/{split}"] = {
                "count": len(unique), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "excluded_broad_source_rows": len(excluded),
                "annotation_basis": dict(Counter(r["annotation_basis"] for r in unique)),
                "context_groups": len({r["group_id"] for r in unique if r.get("messages")})}
            manifest["splits"][f"{language}/{split}"]["polarity_support"] = {
                label: {str(value): sum(r["semantic_targets"][f"requested:{label}"] == 1
                    and r["semantic_targets"][f"denied:{label}"] == value for r in unique)
                    for value in (None, 0, 1)} for label in CAPABILITIES}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(manifest["splits"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/training/semantic-encoder-v2-final"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--families", type=Path, default=Path("data/training/semantic-context-families-v1.json"))
    build(**vars(parser.parse_args()))
