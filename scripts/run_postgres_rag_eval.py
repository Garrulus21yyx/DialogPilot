#!/usr/bin/env python3
"""Run raw-query Knowledge candidate evaluation on a dedicated PostgreSQL DB."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.postgres_rag_candidate_eval import (  # noqa: E402
    CHUNK_PROFILES,
    evaluate_postgres_candidates,
)
from evaluation.rag_pipeline.dataset import RagDataset  # noqa: E402
from infrastructure.bge_m3_embedding import (  # noqa: E402
    BGEM3EmbeddingConfig,
)
from infrastructure.dense_embedding_factory import (  # noqa: E402
    KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
    DenseEmbeddingProviderFactory,
)
from infrastructure.hybrid_retrieval_backend import (  # noqa: E402
    PostgresHybridBackend,
)
from infrastructure.knowledge_embedding import (  # noqa: E402
    LocalHashKnowledgeEmbeddingBaseline,
)
from infrastructure.postgres import (  # noqa: E402
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_knowledge_retriever import (  # noqa: E402
    PostgresKnowledgeCandidateSource,
)
from infrastructure.postgres_knowledge_store import (  # noqa: E402
    PostgresKnowledgeStore,
)
from infrastructure.retrieval_postgres import (  # noqa: E402
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)
from mcp.source_document import SourceDocument  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate raw-query PG FTS + BGE-M3 candidates only; reranking, "
            "parent expansion, packing and generation are not run."
        ),
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument(
        "--split", required=True, choices=("dev", "heldout", "stress"),
    )
    parser.add_argument("--locale", required=True)
    parser.add_argument(
        "--chunk-config", required=True, choices=tuple(CHUNK_PROFILES),
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def preflight_environment(values: Mapping[str, str]) -> str:
    """Require a dedicated DB and a fully explicit local BGE selection."""
    eval_url = str(values.get("EVAL_DATABASE_URL") or "").strip()
    if not eval_url:
        raise ValueError("EVAL_DATABASE_URL is required")
    if str(values.get(KNOWLEDGE_DENSE_EMBEDDING_PROVIDER) or "").strip().lower() != "bge_m3":
        raise ValueError(
            "KNOWLEDGE_DENSE_EMBEDDING_PROVIDER must be explicitly set to bge_m3"
        )
    for production_key in ("DATABASE_URL", "RETRIEVAL_DATABASE_URL"):
        production_url = str(values.get(production_key) or "").strip()
        if production_url and _database_identity(eval_url) == _database_identity(production_url):
            raise ValueError(
                f"EVAL_DATABASE_URL must not target the same database as {production_key}"
            )
    BGEM3EmbeddingConfig.from_env(values)
    return eval_url


def run(args: argparse.Namespace, values: Mapping[str, str]) -> dict[str, object]:
    eval_url = preflight_environment(values)
    dataset = RagDataset.load(args.dataset, verify_checksum=True)
    profile = CHUNK_PROFILES[args.chunk_config]
    provider = DenseEmbeddingProviderFactory(values).build(
        selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
    )
    PostgresMigrationRunner(
        eval_url, actor="postgres-rag-eval", application_version="candidate-eval-v1",
    ).upgrade()
    platform = PostgresPool(PostgresPoolConfig(
        eval_url, min_size=1, max_size=2, timeout_seconds=10,
    ))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        eval_url, min_size=1, max_size=2, pool_timeout_seconds=10,
        statement_timeout_ms=10_000,
    ))
    platform.open()
    retrieval.open()
    try:
        tenant_id = _tenant_id(dataset, args.chunk_config, args.locale)
        store = PostgresKnowledgeStore(
            platform,
            tenant_id=tenant_id,
            locale=args.locale,
            chunk_strategy=profile.strategy,
            chunk_max_tokens=profile.max_tokens,
            chunk_overlap_tokens=profile.overlap_tokens,
            embedding_provider=provider,
        )
        documents = tuple(
            SourceDocument.create(
                source_id=item.document_id,
                title=item.title or item.document_id,
                content=item.content,
                source_type="text",
            )
            for item in dataset.documents
        )
        started = time.perf_counter()
        chunk_count = store.add_documents(documents)
        index_prepare_latency_ms = (time.perf_counter() - started) * 1000
        generation = store.active_generation()
        source = PostgresKnowledgeCandidateSource(
            backend=PostgresHybridBackend(retrieval),
            generations=PostgresRetrievalGenerationRegistry(platform),
            pool=retrieval,
            embed_query=store.embed_query,
        )
        return asyncio.run(evaluate_postgres_candidates(
            dataset=dataset,
            split=args.split,
            tenant_id=tenant_id,
            locale=args.locale,
            generation=generation,
            candidate_source=source,
            chunk_profile_id=args.chunk_config,
            chunk_count=chunk_count,
            index_prepare_latency_ms=index_prepare_latency_ms,
            output_dir=args.output,
            run_id=uuid.uuid4().hex,
        ))
    finally:
        retrieval.close()
        platform.close()


def _tenant_id(dataset: RagDataset, chunk_config: str, locale: str) -> str:
    identity = "\0".join((
        str(dataset.manifest["dataset_id"]),
        str(dataset.manifest["corpus_sha256"]),
        chunk_config,
        locale,
    ))
    return f"rag-eval-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def _database_identity(database_url: str) -> tuple[str, int, str]:
    parsed = urlsplit(database_url)
    return (
        (parsed.hostname or "").lower(),
        parsed.port or 5432,
        unquote(parsed.path).rstrip("/"),
    )


def main(argv: list[str] | None = None) -> int:
    try:
        report = run(parse_args(argv), os.environ)
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if int(report["system_failure_count"]) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
