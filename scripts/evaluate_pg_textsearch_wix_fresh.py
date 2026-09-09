"""Fresh 100-query Wix comparison: current BM25 SQL versus pg_textsearch."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import psycopg

from application.chinese_lexical import postgres_lexical_document
from application.hybrid_retrieval import (
    HybridRetrievalRequest,
    KnowledgeSearchScope,
    RetrievalCorpus,
)
from infrastructure.bge_m3_embedding import BGEM3EmbeddingConfig, LocalBGEM3EmbeddingProvider
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from mcp.rank_fusion import fuse_rankings
from scripts.benchmark_bm25_index_access import Capture
from scripts.compare_pg_native_fts import metrics
from scripts.evaluate_pg_textsearch_wix import SOURCE_REPORT, source_url, target_url
from scripts.prepare_wixqa_local_index import digest


RAW_ROOT = Path("/tmp/dialogpilot-rag-external-lock-20260907")
CACHE = Path("/tmp/dialogpilot-wixqa-full-index-20260908")
PRIOR_MANIFEST = Path("artifacts/eval/wixqa-connected-comparison-2026-09-08/manifest.json")


def select_fresh() -> list[dict]:
    prior = json.loads(PRIOR_MANIFEST.read_text())
    consumed = {
        (row["config"], row["row_index"])
        for split in prior["selected"].values()
        for row in split
    }
    selected = []
    for config in ("wix-expertwritten", "wix-simulated"):
        rows = [json.loads(line) for line in (RAW_ROOT / f"{config}.jsonl").open()]
        choices = [
            (hashlib.sha256(f"e17:{config}:{index}".encode()).hexdigest(), index, row)
            for index, row in enumerate(rows)
            if (config, index) not in consumed
        ]
        for _, index, row in sorted(choices)[:50]:
            selected.append({
                "group_id": hashlib.sha256(f"{config}:{index}".encode()).hexdigest(),
                "config": config,
                "row_index": index,
                "article_ids": row["article_ids"],
                "query": row["question"],
            })
    return selected


def load_dense(cases: list[dict]):
    complete = json.loads((CACHE / "COMPLETE.json").read_text())
    assert digest(CACHE / "chunks.json.gz") == complete["chunks_sha256"]
    chunks = json.loads(gzip.decompress((CACHE / "chunks.json.gz").read_bytes()))
    vectors = [
        np.load(CACHE / f"vectors-{start:06d}.npy", allow_pickle=False)
        for start in range(0, len(chunks), 128)
    ]
    matrix = np.concatenate(vectors)
    identity = json.loads((CACHE / "identity.json").read_text())
    model_path = (
        Path("/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots")
        / identity["model_revision"]
    )
    provider = LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(
        model_path,
        model_path.name,
        identity["model_files"]["pytorch_model.bin"],
        device="cuda",
        batch_size=16,
    ))
    query_vectors = np.asarray(
        provider.embed_queries([case["query"] for case in cases]), dtype=np.float32
    )
    return chunks, query_vectors @ matrix.T, complete


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = select_fresh()
    assert len(cases) == 100
    chunks, dense_scores, index_identity = load_dense(cases)
    generation = json.loads(SOURCE_REPORT.read_text())["generation_id"]
    backend = PostgresHybridBackend(None)
    results = []
    with psycopg.connect(source_url(), autocommit=True, prepare_threshold=None) as source, psycopg.connect(
        target_url(), autocommit=True, prepare_threshold=None,
    ) as target:
        source.execute("SET default_transaction_read_only=on")
        source.execute("SET statement_timeout='30s'")
        mappings = source.execute("""
            SELECT candidate_id, source_id, source_span
            FROM retrieval.knowledge_chunk_search WHERE generation_id=%s
        """, (generation,)).fetchall()
        by_provenance = {
            f"{source_id}:{span['start_char']}:{span['end_char']}": candidate_id
            for candidate_id, source_id, span in mappings
        }
        sources = {candidate_id: source_id for candidate_id, source_id, _ in mappings}
        chunk_candidate_ids = [by_provenance[item["id"]] for item in chunks]
        assert set(sources) == set(chunk_candidate_ids)
        for index, case in enumerate(cases):
            dense_order = sorted(
                range(len(chunks)),
                key=lambda item: (-float(dense_scores[index, item]), chunk_candidate_ids[item]),
            )[:20]
            dense = [chunk_candidate_ids[item] for item in dense_order]
            request = HybridRetrievalRequest(
                tenant_id="wixqa-eval",
                corpus=RetrievalCorpus.KNOWLEDGE,
                backend_fingerprint="e17-fresh",
                generation_id=generation,
                policy_fingerprint="e17-fresh",
                query_text=case["query"],
                query_embedding=None,
                scope=KnowledgeSearchScope(
                    "public", "en", as_of=datetime(2026, 9, 8, tzinfo=timezone.utc)
                ),
                dense_limit=0,
                lexical_limit=20,
            )
            capture = Capture()
            backend._bm25(capture, request)
            started = time.perf_counter()
            current_rows = source.execute(
                capture.query, capture.params, **capture.options
            ).fetchall()
            current_ms = (time.perf_counter() - started) * 1000
            current = [row[0] for row in current_rows]
            lexical_query = postgres_lexical_document(case["query"])
            statement = """
                SELECT candidate_id FROM wix_chunks
                ORDER BY lexical_document <@>
                         to_bm25query(%s,'wix_chunks_bm25'), candidate_id
                LIMIT 20
            """
            started = time.perf_counter()
            extension = [
                row[0] for row in target.execute(
                    statement, (lexical_query,), prepare=False
                ).fetchall()
            ]
            extension_ms = (time.perf_counter() - started) * 1000
            arms = {}
            gold = case["article_ids"]
            for name, lexical in (("current", current), ("pg_textsearch", extension)):
                fused = fuse_rankings(
                    {"dense": dense, "lexical": lexical},
                    weights={"dense": .5, "lexical": .5},
                    rrf_k=10,
                    top_k=20,
                )
                arms[name] = {
                    "lexical": lexical,
                    "fused": fused,
                    "lexical_metrics": metrics(lexical, gold, sources),
                    "fused_metrics": metrics(fused, gold, sources),
                }
            results.append({
                "case": case,
                "dense": dense,
                "fused": {name: value["fused"] for name, value in arms.items()},
                "arms": arms,
                "times_ms": {"current": current_ms, "pg_textsearch": extension_ms},
            })
            print(index + 1, round(current_ms, 1), round(extension_ms, 1), flush=True)
    summary = {}
    for arm in ("current", "pg_textsearch"):
        summary[arm] = {
            "median_query_ms": statistics.median(row["times_ms"][arm] for row in results),
            "lexical": {
                key: statistics.mean(row["arms"][arm]["lexical_metrics"][key] for row in results)
                for key in results[0]["arms"][arm]["lexical_metrics"]
            },
            "fused": {
                key: statistics.mean(row["arms"][arm]["fused_metrics"][key] for row in results)
                for key in results[0]["arms"][arm]["fused_metrics"]
            },
        }
    manifest = {
        "cases": len(cases),
        "selection": "50 expertwritten + 50 simulated; SHA256 order; excludes prior 40",
        "source_database": "dialogpilot_wixqa_eval_20260908",
        "database": "dialogpilot_wixqa_eval_20260908",
        "generation": generation,
        "index_identity": index_identity,
        "api_calls": 0,
        "new_query_embeddings": len(cases),
        "route_k": 20,
        "fused_k": 20,
        "rrf": {"dense": .5, "lexical": .5, "k": 10},
        "summary": summary,
    }
    (args.output / "cases.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    )
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
