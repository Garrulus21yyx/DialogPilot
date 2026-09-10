"""Run frozen Chinese ecommerce retrieval with zhparser + pg_textsearch BM25."""
from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import statistics
import subprocess
import time
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

import psycopg
from psycopg import sql

from application.hybrid_retrieval import RetrievalCorpus
from infrastructure.hybrid_retrieval_backend import (
    PostgresHybridBackend,
    _rows_to_candidates,
    _scope_sql,
)
from infrastructure.postgres import PostgresMigrationRunner
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from scripts import run_rag_tool_calibration as calibration
from scripts.run_native_fts_ecommerce_acceptance import (
    CORPUS,
    INPUTS,
    MODEL,
    REWRITE,
    CachedProvider,
)
from evaluation import ecommerce_pure_rag


ROOT = Path("artifacts/eval/pg-textsearch-zhparser-ecommerce40-2026-09-10")
DOCUMENT_COUNT = 6287
CONTAINER = "dialogpilot-pg-zhparser-eval"
PORT = 55436
LEXICAL_OBSERVATIONS: list[dict[str, object]] = []
INDEX_OBSERVATION: dict[str, object] = {}


def index_name(generation_id: str) -> str:
    return "knowledge_zh_bm25_" + hashlib.sha256(generation_id.encode()).hexdigest()[:20]


def install_chinese_config(connection) -> None:
    connection.execute("CREATE EXTENSION zhparser")
    connection.execute("CREATE TEXT SEARCH CONFIGURATION public.chinese_zh (PARSER=zhparser)")
    connection.execute(
        "ALTER TEXT SEARCH CONFIGURATION public.chinese_zh "
        "ADD MAPPING FOR n,v,a,i,e,l WITH simple"
    )


class ZhparserPgTextsearchKnowledgeStore(PostgresKnowledgeStore):
    """Evaluation store that creates one immutable-generation Chinese BM25 index."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._imported = 0

    def import_documents(self, documents):
        result = super().import_documents(documents)
        self._imported += len(documents)
        if self._imported == DOCUMENT_COUNT:
            name = index_name(result.generation_id)
            started = time.perf_counter()
            with self._pool.transaction() as connection:
                connection.execute(sql.SQL("""
                    CREATE INDEX {index} ON retrieval.knowledge_chunk_search
                    USING bm25(retrieval_text)
                    WITH (text_config='public.chinese_zh', k1=1.2, b=0.75)
                    WHERE tenant_id={tenant} AND generation_id={generation}
                """).format(
                    index=sql.Identifier(name),
                    tenant=sql.Literal(self._tenant_id),
                    generation=sql.Literal(result.generation_id),
                ))
                size = connection.execute(
                    "SELECT pg_relation_size(%s::regclass)",
                    ("retrieval." + name,),
                ).fetchone()[0]
                rows = connection.execute(
                    "SELECT count(*) FROM retrieval.knowledge_chunk_search "
                    "WHERE tenant_id=%s AND generation_id=%s",
                    (self._tenant_id, result.generation_id),
                ).fetchone()[0]
            INDEX_OBSERVATION.update(
                name="retrieval." + name,
                generation_id=result.generation_id,
                rows=int(rows),
                build_ms=(time.perf_counter() - started) * 1000,
                size_bytes=int(size),
            )
        return result


def zhparser_pg_textsearch_bm25(self, connection, request):
    if request.corpus is not RetrievalCorpus.KNOWLEDGE:
        return ()
    table, source_column, revision_column, freshness_column, filters, params = _scope_sql(request)
    if table != "knowledge_chunk_search":
        return ()
    lexeme_count = connection.execute(
        "SELECT length(to_tsvector('public.chinese_zh', %s))",
        (request.query_text,),
        prepare=False,
    ).fetchone()[0]
    index = "retrieval." + index_name(request.generation_id)
    query = sql.SQL("""
        SELECT candidate_id, {source_column}, {revision_column},
               provenance_sha256,
               -(retrieval_text <@> to_bm25query(%s, %s)) AS score,
               {freshness_column}
        FROM retrieval.{table}
        WHERE tenant_id=%s AND generation_id=%s AND {filters}
        ORDER BY retrieval_text <@> to_bm25query(%s, %s), candidate_id
        LIMIT %s
    """).format(
        table=sql.Identifier(table),
        source_column=sql.Identifier(source_column),
        revision_column=sql.Identifier(revision_column),
        freshness_column=sql.Identifier(freshness_column),
        filters=filters,
    )
    started = time.perf_counter()
    try:
        rows = connection.execute(query, (
            request.query_text,
            index,
            request.tenant_id,
            request.generation_id,
            *params,
            request.query_text,
            index,
            request.lexical_limit,
        ), prepare=False).fetchall()
    except psycopg.Error as exc:
        LEXICAL_OBSERVATIONS.append({
            "query_sha256": hashlib.sha256(request.query_text.encode()).hexdigest(),
            "query_lexemes": int(lexeme_count),
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "status": type(exc).__name__,
        })
        raise
    LEXICAL_OBSERVATIONS.append({
        "query_sha256": hashlib.sha256(request.query_text.encode()).hexdigest(),
        "query_lexemes": int(lexeme_count),
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "status": "OK",
        "returned": len(rows),
    })
    return _rows_to_candidates(rows, request)


async def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=False)
    os.environ.update(
        PGOPTIONS="-c max_parallel_maintenance_workers=0",
        MODEL_PROVIDER="deepseek",
        RAG_RERANKER="local_bge",
        RAG_LOCAL_RERANKER_PATH="/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3",
        RAG_LOCAL_RERANKER_DEVICE="cuda",
        HF_HUB_OFFLINE="1",
        KNOWLEDGE_FILTER_CATALOG_FILE=str(
            Path("artifacts/eval/ecommerce-complex-v2-scoped-corpus-v2/catalog.json").resolve()
        ),
    )
    provider = None

    def factory(config):
        nonlocal provider
        if provider is None:
            provider = CachedProvider(config)
        return provider

    config = json.loads(subprocess.check_output(["docker", "inspect", CONTAINER]))[0]
    env = dict(value.split("=", 1) for value in config["Config"]["Env"] if "=" in value)
    base = (
        "postgresql://" + quote(env.get("POSTGRES_USER", "postgres"), safe="")
        + ":" + quote(env["POSTGRES_PASSWORD"], safe="")
        + f"@127.0.0.1:{PORT}/"
    )
    name = "dialogpilot_pg_textsearch_zhparser_ecommerce40"
    database_url = base + name
    with psycopg.connect(base + "postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        PostgresMigrationRunner(database_url).upgrade()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute("CREATE EXTENSION pg_textsearch")
            install_chinese_config(connection)
        args = SimpleNamespace(
            candidate_scope_probe=False,
            model=MODEL,
            scenario="basic",
            corpus_file=CORPUS,
            distractors=None,
            output=ROOT,
            pure_rag_inputs=INPUTS,
            pure_rewrite_seed=None,
            pure_rewrite_cache=REWRITE,
            pure_scope_pair=True,
            max_api_calls=0,
            mixed_business=False,
            full_chain=False,
        )
        with (
            patch.object(
                calibration,
                "RetrievalPoolConfig",
                partial(calibration.RetrievalPoolConfig, statement_timeout_ms=750),
            ),
            patch.object(calibration, "LocalBGEM3EmbeddingProvider", factory),
            patch.object(
                ecommerce_pure_rag,
                "run",
                partial(
                    ecommerce_pure_rag.run,
                    only_scope_filtered=True,
                    as_of=datetime(2026, 9, 10, tzinfo=timezone.utc),
                ),
            ),
            patch.object(
                calibration,
                "PostgresKnowledgeStore",
                partial(ZhparserPgTextsearchKnowledgeStore, chunk_strategy="structure_aware"),
            ),
            patch.object(PostgresHybridBackend, "_bm25", zhparser_pg_textsearch_bm25),
        ):
            await calibration.evaluate(args, database_url)
        ok = [row for row in LEXICAL_OBSERVATIONS if row["status"] == "OK"]
        elapsed = sorted(float(row["elapsed_ms"]) for row in ok)
        manifest = {
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "actual_lexical_ranker": "PG_TEXTSEARCH_1_4_0_ZHPARSER_2_3",
            "database_image": "PostgreSQL-18 + pgvector-0.8.6 + pg_textsearch-1.4.0 + zhparser-2.3 + SCWS-1.2.3",
            "cases": 40,
            "documents": DOCUMENT_COUNT,
            "api_calls": 0,
            "statement_timeout_ms": 750,
            "embedding_counts": provider.counts,
            "index": INDEX_OBSERVATION,
            "lexical": {
                "attempts": len(LEXICAL_OBSERVATIONS),
                "ok": len(ok),
                "p50_ms": statistics.median(elapsed) if elapsed else None,
                "p95_ms": elapsed[max(0, int(len(elapsed) * 0.95) - 1)] if elapsed else None,
                "query_lexemes_min": min(int(row["query_lexemes"]) for row in LEXICAL_OBSERVATIONS),
                "query_lexemes_median": statistics.median(
                    int(row["query_lexemes"]) for row in LEXICAL_OBSERVATIONS
                ),
                "query_lexemes_max": max(int(row["query_lexemes"]) for row in LEXICAL_OBSERVATIONS),
            },
            "hashes": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (CORPUS, INPUTS, REWRITE, Path(__file__))
            },
        }
        (ROOT / "lexical-observations.json").write_text(
            json.dumps(LEXICAL_OBSERVATIONS, indent=2) + "\n"
        )
        (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(manifest, indent=2))
    finally:
        with psycopg.connect(base + "postgres", autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (name,),
            )
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
        gc.collect()


if __name__ == "__main__":
    asyncio.run(main())
