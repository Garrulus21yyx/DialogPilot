#!/usr/bin/env python3
"""Run the frozen Knowledge heldout baseline on a dedicated PostgreSQL DB."""

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
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.model_policy import ModelPolicy, ModelRole  # noqa: E402
from application.cost_budget_policy import (  # noqa: E402
    OFFLINE_KNOWLEDGE_INGEST_BUDGET,
)
from evaluation.postgres_rag_candidate_eval import CHUNK_PROFILES  # noqa: E402
from evaluation.postgres_rag_heldout_eval import (  # noqa: E402
    CHUNK_PROFILE_ID,
    build_heldout_policy,
    evaluate_postgres_heldout,
    validate_heldout_dataset,
)
from evaluation.rag_heldout_artifacts import write_heldout_artifacts  # noqa: E402
from evaluation.rag_pipeline.dataset import RagDataset  # noqa: E402
from infrastructure.dense_embedding_factory import (  # noqa: E402
    KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
    DenseEmbeddingProviderFactory,
)
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend  # noqa: E402
from infrastructure.knowledge_embedding import LocalHashKnowledgeEmbeddingBaseline  # noqa: E402
from infrastructure.postgres import (  # noqa: E402
    PostgresMigrationRunner,
    PostgresPool,
    PostgresPoolConfig,
)
from infrastructure.postgres_knowledge_retriever import (  # noqa: E402
    PostgresKnowledgeCandidateSource,
)
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore  # noqa: E402
from infrastructure.retrieval_postgres import (  # noqa: E402
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)
from mcp.query_transformer import QueryTransformer  # noqa: E402
from mcp.source_document import SourceDocument  # noqa: E402
from scripts.run_postgres_rag_eval import preflight_environment  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--rewrite-concurrency", type=int, default=3)
    return parser.parse_args(argv)


def load_environment(path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    values = {
        key: str(value)
        for key, value in dotenv_values(path).items()
        if value is not None
    }
    values.update(environ)
    return values


def run(args: argparse.Namespace, values: Mapping[str, str]) -> dict[str, Any]:
    if args.output.exists():
        raise ValueError("heldout output must not already exist")
    eval_url = preflight_environment(values)
    dataset = RagDataset.load(args.dataset, verify_checksum=True)
    validate_heldout_dataset(dataset)
    model_policy = ModelPolicy.from_env(values)
    api_key = str(values.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is required for live heldout rewrite")
    client_args: dict[str, str] = {"api_key": api_key}
    if model_policy.base_url:
        client_args["base_url"] = model_policy.base_url
    transformer = QueryTransformer(
        AsyncAnthropic(**client_args), model_policy.profile(ModelRole.REWRITE)
    )
    provider = DenseEmbeddingProviderFactory(values).build(
        selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
    )
    PostgresMigrationRunner(
        eval_url,
        actor="postgres-rag-heldout-eval",
        application_version="heldout-fixed-baseline-v1",
    ).upgrade()
    platform = PostgresPool(
        PostgresPoolConfig(eval_url, min_size=1, max_size=2, timeout_seconds=10)
    )
    retrieval = RetrievalPostgresPool(
        RetrievalPoolConfig(
            eval_url,
            min_size=1,
            max_size=2,
            pool_timeout_seconds=10,
            statement_timeout_ms=10_000,
        )
    )
    platform.open()
    retrieval.open()
    try:
        tenant_id = _tenant_id(dataset)
        profile = CHUNK_PROFILES[CHUNK_PROFILE_ID]
        store = PostgresKnowledgeStore(
            platform,
            tenant_id=tenant_id,
            locale="en",
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
        chunk_count = ingest_documents(store, documents)
        index_prepare_latency_ms = (time.perf_counter() - started) * 1000
        generation = store.active_generation()
        source = PostgresKnowledgeCandidateSource(
            backend=PostgresHybridBackend(retrieval),
            generations=PostgresRetrievalGenerationRegistry(platform),
            pool=retrieval,
            embed_query=store.embed_query,
        )
        run_id = uuid.uuid4().hex
        predictions = asyncio.run(
            evaluate_postgres_heldout(
                dataset=dataset,
                tenant_id=tenant_id,
                locale="en",
                generation=generation,
                candidate_source=source,
                transformer=transformer,
                run_id=run_id,
                rewrite_concurrency=args.rewrite_concurrency,
            )
        )
        return write_heldout_artifacts(
            output_dir=args.output,
            dataset=dataset,
            predictions=predictions,
            run_id=run_id,
            generation=generation,
            policy=build_heldout_policy(generation),
            model_policy=model_policy,
            chunk_count=chunk_count,
            index_prepare_latency_ms=index_prepare_latency_ms,
        )
    finally:
        retrieval.close()
        platform.close()


def _tenant_id(dataset: RagDataset) -> str:
    identity = "\0".join(
        (
            str(dataset.manifest["dataset_id"]),
            str(dataset.manifest["corpus_sha256"]),
            str(dataset.manifest["cases_sha256"]),
            CHUNK_PROFILE_ID,
            "dense-only-standalone-fallback-off-top5-budget2600",
        )
    )
    return f"rag-heldout-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


def ingest_documents(store, documents) -> int:
    """Use the store's bounded incremental-ingest contract for the full corpus."""
    batch_size = OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_sources_per_batch
    chunk_count = 0
    for start in range(0, len(documents), batch_size):
        chunk_count = store.add_documents(documents[start : start + batch_size])
    return chunk_count


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = run(args, load_environment(args.env_file, os.environ))
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if int(report["system_failure_count"]) == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
