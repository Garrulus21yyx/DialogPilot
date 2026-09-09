"""Evaluate pg_textsearch BM25 on the frozen Wix corpus without model calls."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import statistics
import subprocess
import time
import os
from pathlib import Path
from urllib.parse import quote

import psycopg

from application.chinese_lexical import postgres_lexical_document
from mcp.rank_fusion import fuse_rankings
from scripts.compare_pg_native_fts import metrics


SOURCE_DB = "dialogpilot_wixqa_eval_20260908"
SOURCE_REPORT = Path("artifacts/eval/wixqa-postgres-import-2026-09-08/report.json")


def source_url() -> str:
    config = json.loads(subprocess.check_output(
        ["docker", "inspect", "dialogpilot-target-v1-test"], text=True,
    ))[0]
    values = dict(
        value.split("=", 1) for value in config["Config"]["Env"] if "=" in value
    )
    return (
        "postgresql://" + quote(values["POSTGRES_USER"], safe="") + ":"
        + quote(values["POSTGRES_PASSWORD"], safe="")
        + "@127.0.0.1:55432/" + SOURCE_DB
    )


def target_url() -> str:
    container = os.environ.get(
        "PG_TEXTSEARCH_EVAL_CONTAINER", "dialogpilot-pg-textsearch-eval"
    )
    port = os.environ.get("PG_TEXTSEARCH_EVAL_PORT", "55434")
    config = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    values = dict(
        value.split("=", 1) for value in config["Config"]["Env"] if "=" in value
    )
    return (
        "postgresql://" + quote(values.get("POSTGRES_USER", "postgres"), safe="")
        + ":" + quote(values["POSTGRES_PASSWORD"], safe="")
        + "@127.0.0.1:" + port + "/postgres"
    )
def prepare_target(target: psycopg.Connection, source: psycopg.Connection, generation: str):
    target.execute("DROP TABLE IF EXISTS wix_chunks")
    target.execute("""
        CREATE TABLE wix_chunks(
            candidate_id text PRIMARY KEY,
            source_id text NOT NULL,
            lexical_document text NOT NULL
        )
    """)
    started = time.monotonic()
    rows = source.execute("""
        SELECT candidate_id, source_id, lexical_document
        FROM retrieval.knowledge_chunk_search
        WHERE generation_id=%s
        ORDER BY candidate_id
    """, (generation,)).fetchall()
    with target.cursor().copy(
        "COPY wix_chunks(candidate_id,source_id,lexical_document) FROM STDIN"
    ) as copy:
        for row in rows:
            copy.write_row(row)
    loaded_seconds = time.monotonic() - started
    started = time.monotonic()
    target.execute("""
        CREATE INDEX wix_chunks_bm25 ON wix_chunks USING bm25(lexical_document)
        WITH (text_config='simple', k1=1.2, b=0.75)
    """)
    index_seconds = time.monotonic() - started
    target.execute("ANALYZE wix_chunks")
    index_bytes = target.execute(
        "SELECT pg_relation_size('wix_chunks_bm25')"
    ).fetchone()[0]
    return {
        "rows": len(rows),
        "load_seconds": loaded_seconds,
        "index_seconds": index_seconds,
        "index_bytes": index_bytes,
    }


def evaluate_split(target: psycopg.Connection, split: str, output: Path):
    path = Path(
        f"artifacts/eval/wixqa-pg-compact-{split}20-2026-09-08/cases.jsonl.gz"
    )
    rows = [json.loads(line) for line in gzip.open(path, "rt")]
    candidate_sources = dict(target.execute(
        "SELECT candidate_id,source_id FROM wix_chunks"
    ).fetchall())
    # Reuse the exact backend candidate IDs from the frozen E15/E16 captures.
    prior_root = Path(
        "artifacts/eval/pg-native-fts-wix20-v2-2026-09-09"
        if split == "dev" else
        "artifacts/eval/pg-native-fts-wix-heldout20-2026-09-09"
    )
    prior = json.loads((prior_root / "cases.json").read_text())
    cases = []
    for row, prior_row in zip(rows, prior, strict=True):
        assert row["case"] == prior_row["case"]
        dense = prior_row["dense"]
        query = postgres_lexical_document(row["case"]["query"])
        timings = []
        ranking = None
        statement = """
            SELECT candidate_id,
                   lexical_document <@> to_bm25query(%s,'wix_chunks_bm25') AS score
            FROM wix_chunks
            ORDER BY lexical_document <@> to_bm25query(%s,'wix_chunks_bm25'),
                     candidate_id
            LIMIT 20
        """
        for _ in range(3):
            started = time.monotonic()
            result = target.execute(statement, (query, query), prepare=False).fetchall()
            timings.append((time.monotonic() - started) * 1000)
            ids = [item[0] for item in result]
            if ranking is not None:
                assert ids == ranking
            ranking = ids
        target.execute("SET statement_timeout='750ms'")
        try:
            target.execute(statement, (query, query), prepare=False).fetchall()
            within_budget = True
        except psycopg.errors.QueryCanceled:
            within_budget = False
        finally:
            target.execute("SET statement_timeout='30s'")
        fused = fuse_rankings(
            {"dense": dense, "lexical": ranking},
            weights={"dense": .5, "lexical": .5}, rrf_k=10, top_k=20,
        )
        gold = row["case"]["article_ids"]
        cases.append({
            "case": row["case"],
            "dense": dense,
            "lexical": ranking,
            "fused": {"pg_textsearch": fused},
            "scores": {
                "lexical": metrics(ranking, gold, candidate_sources),
                "fused": metrics(fused, gold, candidate_sources),
            },
            "times_ms": timings,
            "passed_750ms": within_budget,
        })
        print(split, len(cases), round(statistics.median(timings), 2), flush=True)
    medians = [statistics.median(row["times_ms"]) for row in cases]
    report = {
        "cases": len(cases),
        "median_query_ms": statistics.median(medians),
        "empirical_p95_query_ms": sorted(medians)[math.ceil(.95*len(medians))-1],
        "timeouts_750ms": sum(not row["passed_750ms"] for row in cases),
        **{
            stage: {
                key: statistics.mean(row["scores"][stage][key] for row in cases)
                for key in cases[0]["scores"][stage]
            }
            for stage in ("lexical", "fused")
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / f"{split}-cases.json.gz").write_bytes(gzip.compress(
        json.dumps(cases, ensure_ascii=False).encode(), mtime=0,
    ))
    (output / f"{split}-cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    (output / f"{split}-report.json").write_text(json.dumps(report, indent=2)+"\n")
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    generation = json.loads(SOURCE_REPORT.read_text())["generation_id"]
    with psycopg.connect(source_url(), autocommit=True) as source, psycopg.connect(
        target_url(), autocommit=True, prepare_threshold=None,
    ) as target:
        target.execute("SET default_transaction_read_only=off")
        target.execute("SET statement_timeout='30s'")
        setup = prepare_target(target, source, generation)
        reports = {
            split: evaluate_split(target, split, args.output)
            for split in ("dev", "heldout")
        }
        manifest = {
            "extension": "pg_textsearch",
            "extension_version": target.execute(
                "SELECT extversion FROM pg_extension WHERE extname='pg_textsearch'"
            ).fetchone()[0],
            "source_database": SOURCE_DB,
            "database": SOURCE_DB,
            "generation": generation,
            "setup": setup,
            "text_config": "simple",
            "k1": 1.2,
            "b": .75,
            "route_k": 20,
            "fused_k": 20,
            "rrf": {"dense": .5, "lexical": .5, "k": 10},
            "api_calls": 0,
            "source_report_sha256": hashlib.sha256(SOURCE_REPORT.read_bytes()).hexdigest(),
            "scope": "isolated container; Wix current generation only; no production mutation",
            "reports": reports,
        }
        (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
