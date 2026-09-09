"""Run the frozen Chinese ecommerce 40 through pg_textsearch, zero API."""
from __future__ import annotations

import asyncio
import gc
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

import psycopg
from psycopg import sql

from infrastructure.hybrid_retrieval_backend import (
    PostgresHybridBackend,
    _rows_to_candidates,
    _scope_sql,
)
from application.chinese_lexical import postgres_lexical_document
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


ROOT = Path("artifacts/eval/pg-textsearch-ecommerce40-v3-2026-09-09")
DOCUMENT_COUNT = 6287


def index_name(generation_id: str) -> str:
    return "knowledge_bm25_" + hashlib.sha256(generation_id.encode()).hexdigest()[:24]


class PgTextsearchKnowledgeStore(PostgresKnowledgeStore):
    """Evaluation owner that creates the BM25 index for the final generation."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._e17_imported = 0

    def import_documents(self, documents):
        result = super().import_documents(documents)
        self._e17_imported += len(documents)
        if self._e17_imported == DOCUMENT_COUNT:
            name = index_name(result.generation_id)
            with self._pool.transaction() as connection:
                connection.execute(sql.SQL("""
                    CREATE INDEX {index} ON retrieval.knowledge_chunk_search
                    USING bm25(lexical_document)
                    WITH (text_config='simple', k1=1.2, b=0.75)
                    WHERE tenant_id={tenant} AND generation_id={generation}
                """).format(
                    index=sql.Identifier(name),
                    tenant=sql.Literal(self._tenant_id),
                    generation=sql.Literal(result.generation_id),
                ))
        return result


def pg_textsearch_bm25(self, connection, request):
    if request.corpus.value != "KNOWLEDGE":
        return ()
    table, source_column, revision_column, freshness_column, filters, params = _scope_sql(request)
    if table != "knowledge_chunk_search":
        return ()
    query = sql.SQL("""
        WITH scoped AS MATERIALIZED (
            SELECT candidate_id, {source_column}, {revision_column},
                   provenance_sha256, lexical_document, {freshness_column}
            FROM retrieval.{table}
            WHERE tenant_id=%s AND generation_id=%s AND {filters}
        )
        SELECT candidate_id, {source_column}, {revision_column},
               provenance_sha256,
               -(lexical_document <@> to_bm25query(%s, %s)) AS score,
               {freshness_column}
        FROM scoped
        ORDER BY lexical_document <@> to_bm25query(%s, %s), candidate_id
        LIMIT %s
    """).format(
        table=sql.Identifier(table),
        source_column=sql.Identifier(source_column),
        revision_column=sql.Identifier(revision_column),
        freshness_column=sql.Identifier(freshness_column),
        filters=filters,
    )
    index = "retrieval." + index_name(request.generation_id)
    lexical = postgres_lexical_document(request.query_text)
    rows = connection.execute(query, (
        request.tenant_id,
        request.generation_id,
        *params,
        lexical,
        index,
        lexical,
        index,
        request.lexical_limit,
    ), prepare=False).fetchall()
    return _rows_to_candidates(rows, request)


async def main():
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

    config = json.loads(subprocess.check_output([
        "docker", "inspect", "dialogpilot-pgvector-pg-textsearch-eval"
    ]))[0]
    env = dict(value.split("=", 1) for value in config["Config"]["Env"] if "=" in value)
    base = (
        "postgresql://" + quote(env.get("POSTGRES_USER", "postgres"), safe="")
        + ":" + quote(env["POSTGRES_PASSWORD"], safe="")
        + "@127.0.0.1:55435/"
    )
    name = "dialogpilot_pg_textsearch_ecommerce40"
    database_url = base + name
    with psycopg.connect(base + "postgres", autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        PostgresMigrationRunner(database_url).upgrade()
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute("CREATE EXTENSION pg_textsearch")
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
                partial(PgTextsearchKnowledgeStore, chunk_strategy="structure_aware"),
            ),
            patch.object(PostgresHybridBackend, "_bm25", pg_textsearch_bm25),
        ):
            await calibration.evaluate(args, database_url)
        manifest = {
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "actual_lexical_ranker": "PG_TEXTSEARCH_1_4_0_SIMPLE_PRETOKENIZED",
            "database_image": "pgvector-0.8.6 + pg_textsearch-1.4.0 + PostgreSQL-18",
            "cases": 40,
            "documents": DOCUMENT_COUNT,
            "api_calls": 0,
            "statement_timeout_ms": 750,
            "embedding_counts": provider.counts,
            "hashes": {
                str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (CORPUS, INPUTS, REWRITE, Path(__file__))
            },
        }
        (ROOT / "pg-textsearch-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        print(json.dumps(manifest, indent=2))
    finally:
        with psycopg.connect(base + "postgres", autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname=%s AND pid<>pg_backend_pid()",
                (name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        gc.collect()


if __name__ == "__main__":
    asyncio.run(main())
