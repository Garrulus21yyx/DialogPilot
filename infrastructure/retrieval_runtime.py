"""Compose the PostgreSQL retrieval runtime used by the API lifespan."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from application.hybrid_retrieval import EmbeddingProviderKind
from application.memory_retrieval_policy import DEFAULT_MEMORY_RETRIEVAL_POLICY
from application.service_episode_memory_search import ServiceEpisodeMemorySearch
from application.service_episode_retriever import (
    ServiceEpisodeFreshnessMode,
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetrievalPurpose,
    ServiceEpisodeRetriever,
)
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.dense_embedding_factory import (
    SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
    DenseEmbeddingProviderFactory,
)
from infrastructure.postgres_retrieval_projection import (
    PostgresCanonicalRetrievalProjector,
)
from infrastructure.postgres_service_episode import PostgresServiceEpisodeResolver
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)
from infrastructure.service_episode_embedding import (
    LocalHashServiceEpisodeEmbeddingBaseline,
    ServiceEpisodeDocumentEmbedder,
    ServiceEpisodeQueryEmbedder,
)
from infrastructure.service_episode_generation import (
    PostgresServiceEpisodeGenerationManager,
)


@dataclass(frozen=True)
class RetrievalRuntime:
    pool: RetrievalPostgresPool
    service_episode_search: ServiceEpisodeMemorySearch
    projector: PostgresCanonicalRetrievalProjector
    service_episode_generations: PostgresServiceEpisodeGenerationManager


def build_retrieval_runtime(
    platform_pool,
    database_url: str,
    config: Mapping[str, str],
    *,
    embedding_factory: DenseEmbeddingProviderFactory,
) -> RetrievalRuntime:
    """Build retrieval around the provider selected by composition."""

    pool = RetrievalPostgresPool(RetrievalPoolConfig(
        database_url,
        min_size=int(config.get("RETRIEVAL_POOL_MIN_SIZE", "1")),
        max_size=int(config.get("RETRIEVAL_POOL_MAX_SIZE", "4")),
        pool_timeout_seconds=float(config.get("RETRIEVAL_POOL_TIMEOUT_SECONDS", "2")),
        statement_timeout_ms=int(config.get("RETRIEVAL_STATEMENT_TIMEOUT_MS", "750")),
    ))
    provider = embedding_factory.build(
        selection_key=SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashServiceEpisodeEmbeddingBaseline,
    )
    allow_hash_baseline = (
        provider.profile.provider_kind is EmbeddingProviderKind.HASH_BASELINE
    )
    policies = (
        ServiceEpisodeRetrievalPolicy(
            config.get(
                "SERVICE_EPISODE_REFERENCE_POLICY_VERSION",
                "memory-reference-resolution-baseline-v1",
            ),
            DEFAULT_MEMORY_RETRIEVAL_POLICY,
            float(config.get("SERVICE_EPISODE_MINIMUM_FUSED_RELEVANCE", "0")),
            int(config.get("SERVICE_EPISODE_FRESHNESS_MAX_AGE_SECONDS", "31536000")),
            ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
            ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT,
            float(config.get("SERVICE_EPISODE_REFERENCE_MARGIN", "0.001")),
        ),
        ServiceEpisodeRetrievalPolicy(
            config.get(
                "SERVICE_EPISODE_HISTORICAL_POLICY_VERSION",
                "memory-historical-evidence-baseline-v1",
            ),
            DEFAULT_MEMORY_RETRIEVAL_POLICY,
            float(config.get("SERVICE_EPISODE_MINIMUM_FUSED_RELEVANCE", "0")),
            int(config.get("SERVICE_EPISODE_FRESHNESS_MAX_AGE_SECONDS", "31536000")),
            ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE,
            ServiceEpisodeFreshnessMode.HARD_WINDOW,
        ),
    )
    backend = PostgresHybridBackend(pool)
    search = ServiceEpisodeMemorySearch(
        generations=PostgresRetrievalGenerationRegistry(platform_pool),
        retrievers={
            policy.purpose: ServiceEpisodeRetriever(backend, policy)
            for policy in policies
        },
        embed_query=ServiceEpisodeQueryEmbedder(
            provider,
            allow_hash_baseline=allow_hash_baseline,
        ),
    )
    projector = PostgresCanonicalRetrievalProjector(
        platform_pool,
        resolvers={
            "SERVICE_EPISODE": PostgresServiceEpisodeResolver(
                ServiceEpisodeDocumentEmbedder(
                    provider,
                    allow_hash_baseline=allow_hash_baseline,
                ),
            ),
        },
    )
    generations = PostgresServiceEpisodeGenerationManager(
        platform_pool,
        projector=projector,
        embedding_profile=provider.profile,
    )
    return RetrievalRuntime(pool, search, projector, generations)
