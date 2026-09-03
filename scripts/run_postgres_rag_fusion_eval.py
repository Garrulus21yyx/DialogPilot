#!/usr/bin/env python3
"""Capture raw PG routes once and select RRF fusion on designated Dev."""

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

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.postgres_rag_candidate_eval import CHUNK_PROFILES  # noqa: E402
from evaluation.postgres_rag_fusion_capture import (  # noqa: E402
    CHUNK_PROFILE_ID,
    capture_postgres_sources_and_replay,
)
from evaluation.rag_pipeline.dataset import RagDataset  # noqa: E402
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
from scripts.run_postgres_rag_eval import preflight_environment  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "On designated Dev, capture raw lexical/dense Top-40 from one "
            "fixed-512-64 PG+BGE run and replay the declared RRF grid offline."
        ),
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--locale", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, values: Mapping[str, str]) -> dict[str, object]:
    eval_url = preflight_environment(values)
    dataset = RagDataset.load(args.dataset, verify_checksum=True)
    profile = CHUNK_PROFILES[CHUNK_PROFILE_ID]
    provider = DenseEmbeddingProviderFactory(values).build(
        selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
    )
    PostgresMigrationRunner(
        eval_url,
        actor="postgres-rag-fusion-eval",
        application_version="fusion-eval-v1",
    ).upgrade()
    platform = PostgresPool(
        PostgresPoolConfig(
            eval_url,
            min_size=1,
            max_size=2,
            timeout_seconds=10,
        )
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
        tenant_id = _tenant_id(dataset, args.locale)
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
        return asyncio.run(
            capture_postgres_sources_and_replay(
                dataset=dataset,
                split="dev",
                tenant_id=tenant_id,
                locale=args.locale,
                generation=generation,
                candidate_source=source,
                chunk_count=chunk_count,
                index_prepare_latency_ms=index_prepare_latency_ms,
                output_dir=args.output,
                run_id=uuid.uuid4().hex,
            )
        )
    finally:
        retrieval.close()
        platform.close()


def _tenant_id(dataset: RagDataset, locale: str) -> str:
    identity = "\0".join(
        (
            str(dataset.manifest["dataset_id"]),
            str(dataset.manifest["corpus_sha256"]),
            CHUNK_PROFILE_ID,
            "fusion-source-capture-v1",
            locale,
        )
    )
    return f"rag-eval-{hashlib.sha256(identity.encode()).hexdigest()[:24]}"


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
