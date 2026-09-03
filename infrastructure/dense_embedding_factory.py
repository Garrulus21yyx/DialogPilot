"""Select one explicit dense embedding provider for retrieval composition."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from application.hybrid_retrieval import EmbeddingProfile
from infrastructure.bge_m3_embedding import (
    BGEM3EmbeddingConfig,
    LocalBGEM3EmbeddingProvider,
)


KNOWLEDGE_DENSE_EMBEDDING_PROVIDER = "KNOWLEDGE_DENSE_EMBEDDING_PROVIDER"
SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER = (
    "SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER"
)


class DenseEmbeddingProvider(Protocol):
    profile: EmbeddingProfile

    def embed_documents(
        self, texts: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...

    def embed_queries(
        self, texts: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...


class DenseEmbeddingProviderSelectionError(ValueError):
    pass


class DenseEmbeddingProviderFactory:
    """Share one configured MODEL provider while preserving corpus baselines."""

    hash_baseline = "hash_baseline"
    bge_m3 = "bge_m3"
    selection_keys = frozenset({
        KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
    })

    def __init__(self, config: Mapping[str, str]):
        self._config = dict(config)
        self._model_provider: DenseEmbeddingProvider | None = None

    def build(
        self,
        *,
        selection_key: str,
        baseline_factory: Callable[[], DenseEmbeddingProvider],
    ) -> DenseEmbeddingProvider:
        if selection_key not in self.selection_keys:
            raise DenseEmbeddingProviderSelectionError(
                f"unsupported dense embedding selection key: {selection_key}"
            )
        selection = self._config.get(
            selection_key, self.hash_baseline,
        ).strip().lower()
        if selection not in {self.hash_baseline, self.bge_m3}:
            raise DenseEmbeddingProviderSelectionError(
                f"{selection_key} must be hash_baseline or bge_m3"
            )
        if selection == self.hash_baseline:
            return baseline_factory()
        if self._model_provider is None:
            config = BGEM3EmbeddingConfig.from_env(self._config)
            self._model_provider = LocalBGEM3EmbeddingProvider(config)
        return self._model_provider
