"""Generation-validated query embedding for ServiceEpisode retrieval."""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from application.hybrid_retrieval import RetrievalGeneration


class ServiceEpisodeEmbeddingContractError(ValueError):
    pass


class ServiceEpisodeQueryEmbedder:
    """Bind the deterministic local provider to an immutable generation."""

    model_id = "dialogpilot-hash-embedding-v1"
    dimension = 384

    def __init__(self, embedding_function: Callable | None = None):
        if embedding_function is None:
            from core.local_embedding import LocalHashEmbeddingFunction

            embedding_function = LocalHashEmbeddingFunction()
        self._embedding_function = embedding_function

    def __call__(
        self, query: str, generation: RetrievalGeneration,
    ) -> tuple[float, ...]:
        if (
            not query.strip()
            or generation.embedding_model != self.model_id
            or generation.embedding_dimension != self.dimension
            or not generation.replayable_across_environments
        ):
            raise ServiceEpisodeEmbeddingContractError(
                "query embedding generation contract is unsupported"
            )
        vectors = self._embedding_function([query])
        if not isinstance(vectors, Sequence) or len(vectors) != 1:
            raise ServiceEpisodeEmbeddingContractError(
                "query embedding provider returned an invalid batch"
            )
        vector = tuple(float(item) for item in vectors[0])
        if len(vector) != self.dimension or any(
            not math.isfinite(item) for item in vector
        ):
            raise ServiceEpisodeEmbeddingContractError(
                "query embedding provider returned an invalid vector"
            )
        return vector
