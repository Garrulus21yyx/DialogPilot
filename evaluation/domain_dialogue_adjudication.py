"""Apply explicit assistant review decisions to immutable synthetic candidates."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

from application.encoder_input import EncoderInput
from application.domain_encoder import DOMAINS, DEFER_DOMAIN


def adjudicate(row, ledger):
    prefix, language, batch, family, variant = row["case_id"].split(":")
    if prefix != "domain-v3" or language != row["language"]:
        raise ValueError("review case identity mismatch")
    decision = ledger["families"][f"{language}:{int(batch)}"][int(family)]
    codes, explanation = decision.split("|", 1)
    reasons = dict(re.findall(r"(?:^|；)([012])([^；]+)", explanation))
    if len(codes) != 3 or set(reasons) != {"0", "1", "2"}:
        raise ValueError("review must explicitly cover all three variants")
    label = ledger["codes"][codes[int(variant)]]
    if label is not None and label not in {*DOMAINS, DEFER_DOMAIN}:
        raise ValueError("review label outside supported domains")
    return {"case_id": row["case_id"], "old_label": row["label"], "label": label,
            "status": "quarantined" if label is None else "retained" if label == row["label"] else "corrected",
            "reason": reasons[variant].strip().rstrip("。"), "reviewer": ledger["reviewer"]}


def build(source: Path, review: Path, output: Path):
    if output.exists():
        raise ValueError("review output already exists")
    ledger = json.loads(review.read_text())
    paths = sorted(source.glob("*/*.jsonl"))
    hashes = {str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    if hashes != ledger["source_sha256"]:
        raise ValueError("review source snapshot changed")
    pending, audit, seen, groups = {}, [], set(), {}
    summary = {"status": "ASSISTANT_REVIEWED_SYNTHETIC", "scope": ledger["scope"],
               "source_sha256": hashes, "review_sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
               "splits": {}}
    for path in paths:
        key = str(path.relative_to(source))
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        accepted = []
        for row in rows:
            identity = (row["language"], EncoderInput.from_record(row).identity())
            if identity in seen or groups.setdefault(row["group_id"], key) != key:
                raise ValueError("candidate duplicate or cross-split family")
            seen.add(identity)
            decision = adjudicate(row, ledger)
            audit.append({**decision, "source_file": key})
            if decision["label"] is not None:
                accepted.append({**row, "label": decision["label"],
                                 "review_ref": row["case_id"], "source": "assistant-reviewed-synthetic-domain-dialogue"})
        pending[key] = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in accepted)
        summary["splits"][key] = {"input": len(rows), "accepted": len(accepted),
            "labels": dict(Counter(r["label"] for r in accepted)),
            "sha256": hashlib.sha256(pending[key].encode()).hexdigest()}
    if len({r["case_id"] for r in audit}) != len(audit):
        raise ValueError("duplicate review identity")
    summary["decisions"] = dict(Counter(r["status"] for r in audit))
    pending["audit.jsonl"] = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in audit)
    pending["manifest.json"] = json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    for key, text in pending.items():
        destination = output / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    build(**vars(parser.parse_args()))
