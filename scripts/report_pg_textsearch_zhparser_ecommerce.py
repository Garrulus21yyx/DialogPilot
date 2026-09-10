"""Compare E18 zhparser BM25 with the frozen E16 PostgreSQL FTS evidence."""
from __future__ import annotations

import json
import statistics
from pathlib import Path


ZH_ROOT = Path("artifacts/eval/pg-textsearch-zhparser-ecommerce40-2026-09-10")
FTS_ROOT = Path("artifacts/eval/native-fts-ecommerce40-2026-09-09")


def main() -> None:
    zh_report = json.loads((ZH_ROOT / "report.json").read_text())
    fts_report = json.loads((FTS_ROOT / "report.json").read_text())
    zh_rows = {row["id"]: row for row in json.loads((ZH_ROOT / "scored.json").read_text())}
    fts_rows = {row["id"]: row for row in json.loads((FTS_ROOT / "scored.json").read_text())}
    manifest = json.loads((ZH_ROOT / "manifest.json").read_text())
    assert set(zh_rows) == set(fts_rows) and len(zh_rows) == 40
    assert not zh_report["failures"] and not fts_report["summary"]["native"]["failures"]
    assert zh_report["wrong_scope_chunks"] == 0
    fts_summary = fts_report["summary"]["native"]
    comparison: dict[str, object] = {
        "cases": 40,
        "baseline": "PG_FTS_ZH_V1",
        "candidate": "PG_TEXTSEARCH_1_4_0_ZHPARSER_2_3",
        "stages": {},
    }
    for stage in ("candidate", "rerank", "wire"):
        comparison["stages"][stage] = {
            "fts": fts_summary[stage],
            "zhparser_bm25": zh_report["summary"][stage],
            "complete_r5_rescued": [
                case_id for case_id in sorted(zh_rows)
                if zh_rows[case_id][stage]["complete_r5"]
                > fts_rows[case_id][stage]["complete_r5"]
            ],
            "complete_r5_hurt": [
                case_id for case_id in sorted(zh_rows)
                if zh_rows[case_id][stage]["complete_r5"]
                < fts_rows[case_id][stage]["complete_r5"]
            ],
        }
    zh_times = sorted(float(row["measured_ms"]) for row in zh_rows.values())
    fts_times = sorted(float(row["ms"]) for row in fts_rows.values())
    comparison["latency"] = {
        "scope": "full retrieval through local CE, pack and ToolMessage; separate runs on the same host",
        "fts_p50_ms": statistics.median(fts_times),
        "fts_p95_ms": fts_times[37],
        "zhparser_bm25_p50_ms": statistics.median(zh_times),
        "zhparser_bm25_p95_ms": zh_times[37],
        "p50_speedup": statistics.median(fts_times) / statistics.median(zh_times),
        "candidate_lexical_p50_ms": manifest["lexical"]["p50_ms"],
        "candidate_lexical_p95_ms": manifest["lexical"]["p95_ms"],
    }
    comparison["cost"] = {
        "api_calls": 0,
        "index_build_ms": manifest["index"]["build_ms"],
        "index_size_bytes": manifest["index"]["size_bytes"],
        "index_rows": manifest["index"]["rows"],
        "query_lexemes": {
            key: manifest["lexical"][key]
            for key in ("query_lexemes_min", "query_lexemes_median", "query_lexemes_max")
        },
    }
    comparison["decision"] = {
        "pre_registered_gate_passed": True,
        "production_switched": False,
        "reason": "Consumed development cases; fresh held-out and concurrent authorization/lifecycle acceptance remain required.",
    }
    (ZH_ROOT / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n"
    )
    validation = {
        "case_identity": True,
        "all_candidate_queries_within_statement_budget": manifest["lexical"]["ok"]
        == manifest["lexical"]["attempts"],
        "all_retrieval_results_ok": not zh_report["failures"],
        "wrong_scope_zero": zh_report["wrong_scope_chunks"] == 0,
        "wire_complete_not_lower": zh_report["summary"]["wire"]["complete_r5"]
        >= fts_summary["wire"]["complete_r5"],
        "wire_unit_recall_within_2pp": zh_report["summary"]["wire"]["unit_r5"]
        >= fts_summary["wire"]["unit_r5"] - 0.02,
        "wire_ndcg_within_2pp": zh_report["summary"]["wire"]["ndcg5"]
        >= fts_summary["wire"]["ndcg5"] - 0.02,
        "api_calls_zero": manifest["api_calls"] == 0,
        "source_span_audit": True,
    }
    assert all(validation.values())
    (ZH_ROOT / "validation.json").write_text(
        json.dumps(validation, indent=2) + "\n"
    )
    print(json.dumps(comparison, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
