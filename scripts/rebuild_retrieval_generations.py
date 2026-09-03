#!/usr/bin/env python3
"""Build and activate retrieval generations before an evaluation run."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from application.retrieval_generation_rebuild import (  # noqa: E402
    KnowledgeGenerationOwner,
    ServiceEpisodeGenerationOwner,
    rebuild_selected_generations,
)
from infrastructure.dense_embedding_factory import (  # noqa: E402
    KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
    DenseEmbeddingProviderFactory,
)
from infrastructure.knowledge_embedding import (  # noqa: E402
    LocalHashKnowledgeEmbeddingBaseline,
)
from infrastructure.postgres import PostgresPool, PostgresPoolConfig  # noqa: E402
from infrastructure.postgres_knowledge_store import (  # noqa: E402
    PostgresKnowledgeStore,
)
from infrastructure.retrieval_runtime import (  # noqa: E402
    RetrievalRuntime,
    build_retrieval_runtime,
)


@dataclass
class _Owners:
    pool: PostgresPool
    knowledge: KnowledgeGenerationOwner | None
    service_episode: ServiceEpisodeGenerationOwner | None
    retrieval_runtime: RetrievalRuntime | None

    def close(self) -> None:
        if self.retrieval_runtime is not None:
            self.retrieval_runtime.pool.close()
        self.pool.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build immutable Knowledge and/or ServiceEpisode generations using "
            "the dense providers selected in the environment."
        ),
    )
    parser.add_argument(
        "--corpus",
        required=True,
        choices=("knowledge", "service_episode", "all"),
    )
    parser.add_argument(
        "--generation-id",
        help="Required immutable generation ID for ServiceEpisode builds.",
    )
    return parser


def _compose(corpus: str, env: Mapping[str, str]) -> _Owners:
    values = dict(env)
    database_url = str(values.get("DATABASE_URL") or "").strip()
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    pool = PostgresPool(PostgresPoolConfig.from_env(values))
    pool.open()
    factory = DenseEmbeddingProviderFactory(values)
    runtime = None
    knowledge = None
    try:
        if corpus in {"service_episode", "all"}:
            runtime = build_retrieval_runtime(
                pool,
                database_url,
                values,
                embedding_factory=factory,
            )
        if corpus in {"knowledge", "all"}:
            knowledge = PostgresKnowledgeStore(
                pool,
                tenant_id=values.get("DEFAULT_TENANT_ID", "default"),
                chunk_max_tokens=int(values.get("RAG_CHUNK_MAX_TOKENS", "512")),
                chunk_overlap_tokens=int(
                    values.get("RAG_CHUNK_OVERLAP_TOKENS", "64")
                ),
                embedding_provider=factory.build(
                    selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
                    baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
                ),
            )
    except Exception:
        if runtime is not None:
            runtime.pool.close()
        pool.close()
        raise
    return _Owners(
        pool=pool,
        knowledge=knowledge,
        service_episode=(
            runtime.service_episode_generations if runtime is not None else None
        ),
        retrieval_runtime=runtime,
    )


async def _execute(
    corpus: str,
    *,
    generation_id: str | None,
    owners: _Owners,
) -> dict[str, object]:
    results = await rebuild_selected_generations(
        corpus,
        knowledge=owners.knowledge,
        service_episode=owners.service_episode,
        service_episode_generation_id=generation_id,
    )
    return {
        "corpus": corpus,
        "results": [result.to_dict() for result in results],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.corpus in {"service_episode", "all"} and not str(
        args.generation_id or ""
    ).strip():
        parser.error("--generation-id is required for ServiceEpisode builds")
    owners: _Owners | None = None
    try:
        owners = _compose(args.corpus, os.environ)
        output = asyncio.run(_execute(
            args.corpus,
            generation_id=args.generation_id,
            owners=owners,
        ))
    finally:
        if owners is not None:
            owners.close()
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
