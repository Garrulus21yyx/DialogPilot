"""Aggregate all heldout pairs with grouped uncertainty; no dropping failures."""

import argparse, json, gzip, math, hashlib
from pathlib import Path
from collections import Counter, defaultdict
import numpy as np


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pair", type=Path, required=True)
    p.add_argument("--scores", type=Path, required=True)
    a = p.parse_args()
    gold = {
        c["id"]: c
        for c in json.loads(
            Path("data/eval/ecommerce-rag-v1/heldout.gold.json").read_text()
        )
    }
    assert len(gold) == 80
    docs = {
        d["source_id"]: d["content"]
        for d in json.loads(Path("data/eval/ecommerce-rag-v1/corpus.json").read_text())
    }
    audit = {}
    judges = [
        json.loads(l) for l in gzip.decompress((a.scores / "judgements.jsonl.gz").read_bytes()).splitlines()
    ]
    assert len(judges) == 160
    judges = {(r["arm"], r["id"]): r for r in judges}
    assert len(judges) == 160
    result = {}
    rows = {}
    for arm in ["baseline", "candidate"]:
        rr = [
            json.loads(l)
            for l in gzip.decompress(
                (a.pair / arm / "full-cases.jsonl.gz").read_bytes()
            ).splitlines()
        ]
        assert len(rr) == 80 and {r["case_id"] for r in rr} == set(gold)
        out = []
        calls = input_tokens = output_tokens = cache_tokens = 0
        occurrences = 0
        for r in rr:
            cid = r["case_id"]
            source_ids = []
            for t in r["tools"]:
                if t["name"] == "knowledge_search":
                    w = t["result"].get("output_for_model")
                    w = json.loads(w) if isinstance(w, str) else (w or {})
                    for e in w.get("evidence", []):
                        occurrences += 1
                        ref = e["source"]
                        body = docs[ref["source_id"]]
                        assert (
                            body[ref["start_char"] : ref["end_char"]] == e["text"]
                            and hashlib.sha256(body.encode()).hexdigest()
                            == ref["checksum"]
                        ), (arm, cid, e["evidence_id"])
                        if e["source"]["source_id"] not in source_ids:
                            source_ids.append(e["source"]["source_id"])
            target = set(gold[cid]["knowledge_sources"])
            hits = [i for i, s in enumerate(source_ids[:5], 1) if s in target]
            recall = len(set(source_ids[:5]) & target) / len(target)
            dcg = sum(1 / math.log2(i + 1) for i in hits)
            ideal = sum(1 / math.log2(i + 1) for i in range(1, min(5, len(target)) + 1))
            j = judges[arm, cid]
            assessment = j.get("assessment", {})
            names = [t["name"] for t in r["tools"]]
            out.append(
                {
                    "id": cid,
                    "group": gold[cid]["group_id"],
                    "category": gold[cid]["category"],
                    "source_recall": recall,
                    "source_ndcg": dcg / ideal,
                    "judge_verdict": j["verdict"],
                    "issues": assessment.get("issues", []),
                    "tools": names,
                    "unnecessary_order_lookup": not gold[cid]["business_required"]
                    and "order_lookup" in names,
                    "runtime_verified": r.get("outcome", {})
                    .get("response", {})
                    .get("verified", False),
                }
            )
            calls += len(r["api_calls"])
            for c in r["api_calls"]:
                u = c.get("usage") or (c.get("response") or {}).get("usage", {})
                cached = (
                    (u.get("input_token_details") or {}).get("cache_read", 0)
                    if c.get("usage")
                    else u.get("cache_read_input_tokens", 0)
                )
                cache_tokens += cached
                input_tokens += u.get("input_tokens", 0) + (
                    0
                    if c.get("usage")
                    else cached + u.get("cache_creation_input_tokens", 0)
                )
                output_tokens += u.get("output_tokens", 0)
        audit[arm] = {
            "wire_evidence_occurrences": occurrences,
            "source_text_offset_checksum_errors": 0,
        }
        rows[arm] = {r["id"]: r for r in out}
        result[arm] = {
            "n": 80,
            "source_recall": sum(r["source_recall"] for r in out) / 80,
            "source_ndcg": sum(r["source_ndcg"] for r in out) / 80,
            "auto_judge_verdicts": dict(Counter(r["judge_verdict"] for r in out)),
            "auto_judge_pass_rate": sum(r["judge_verdict"] == "PASS" for r in out) / 80,
            "api_calls": calls,
            "input_tokens_including_cache": input_tokens,
            "cache_tokens": cache_tokens,
            "output_tokens": output_tokens,
            "unnecessary_order_lookup_cases": sum(
                r["unnecessary_order_lookup"] for r in out
            ),
            "categories": {
                cat: {
                    "n": len(v),
                    "source_recall": sum(r["source_recall"] for r in v) / len(v),
                    "auto_pass": sum(r["judge_verdict"] == "PASS" for r in v),
                }
                for cat in ["policy", "multiturn", "product", "mixed", "boundary"]
                if (v := [r for r in out if r["category"] == cat])
            },
        }
    pair = []
    for cid in gold:
        b = rows["baseline"][cid]
        c = rows["candidate"][cid]
        pair.append(
            {
                "id": cid,
                "group": c["group"],
                "baseline": b,
                "candidate": c,
                "auto_pass_delta": int(c["judge_verdict"] == "PASS")
                - int(b["judge_verdict"] == "PASS"),
            }
        )
    groups = defaultdict(list)
    for r in pair:
        groups[r["group"]].append(r["auto_pass_delta"])
    sums = np.array([sum(v) for v in groups.values()])
    counts = np.array([len(v) for v in groups.values()])
    ix = np.random.default_rng(20260908).integers(0, len(groups), (20000, len(groups)))
    delta = sums[ix].sum(1) / counts[ix].sum(1)
    report = {
        "source_audit": audit,
        "arms": result,
        "paired": {
            "groups": len(groups),
            "rescue": sum(r["auto_pass_delta"] > 0 for r in pair),
            "hurt": sum(r["auto_pass_delta"] < 0 for r in pair),
            "auto_pass_delta": sums.sum() / 80,
            "cluster_bootstrap_95": np.quantile(delta, [0.025, 0.975]).tolist(),
        },
        "judge_calls": 160,
        "limitations": "Synthetic short sources, same-series independent scoring call, not human gold or error-independent judge. REVIEW/ERROR excluded from PASS numerator but retained in denominator. No production closure implied.",
    }
    audit_path = a.pair / "answer-audit.json"
    if audit_path.exists():
        report["answer_scoring_audit"] = json.loads(audit_path.read_text())
    (a.pair / "paired-cases.json").write_text(
        json.dumps(pair, ensure_ascii=False, indent=2) + "\n"
    )
    (a.pair / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
