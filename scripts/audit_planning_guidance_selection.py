"""Zero-model audit of frozen inputs and paired planning outputs."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from infrastructure.target_model_context import planning_payload_from_request


def audit(root):
    manifest = json.loads((root / "manifest.json").read_text())
    zipped = root / "results.jsonl.gz"
    text = gzip.decompress(zipped.read_bytes()).decode() if zipped.exists() else (root / "results.jsonl").read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    expected = {(c["id"], arm) for c in manifest["cases"] for arm in manifest["arms"]}
    assert {(r["case_id"], r["arm"]) for r in rows} == expected
    assert len(rows) == len(expected) == manifest["calls_budget"]
    examples = {e["id"] for e in manifest["examples"]}
    payloads, schemas, summaries = {}, {}, {}
    for row in rows:
        key = row["case_id"]
        assert len(row["calls"]) == 1
        call = row["calls"][0]
        request = call["request"]
        payload = planning_payload_from_request(request)
        assert payloads.setdefault(key, payload) == payload
        assert schemas.setdefault(key, request["tools"]) == request["tools"]
        case = next(c for c in manifest["cases"] if c["id"] == key)
        assert payload["message"] == case["query"]
        assert [(h["role"], h["content"]) for h in payload["conversation_context"]["recent_messages"]] == [
            (h["role"], h["content"]) for h in case["history"]]
        assert payload["pending_input"] is None and payload["pending_approval"] is None
        assert set(row["example_ids"]) <= examples
        assert len(row["example_ids"]) == (3 if row["arm"] in {"fixed", "dynamic"} else 0)
        summary = summaries.setdefault(row["arm"], {"cases": 0, "actions": {}, "input_tokens": 0,
                                                    "output_tokens": 0, "cache_read_tokens": 0})
        summary["cases"] += 1
        summary["actions"][row["action"]] = summary["actions"].get(row["action"], 0) + 1
        usage = call.get("usage", {})
        summary["input_tokens"] += usage.get("input_tokens", 0)
        summary["output_tokens"] += usage.get("output_tokens", 0)
        summary["cache_read_tokens"] += usage.get("input_token_details", {}).get("cache_read", 0)
    return {"audit": "passed", "input_schema_history_pairs": len(payloads), "calls": len(rows),
            "scope": manifest["scope"], "manifest_sha256": hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest(),
            "arms": summaries, "note": "Action counts are not semantic correctness or business completion."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = audit(args.root)
    (args.root / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(result, ensure_ascii=False))
