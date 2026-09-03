"""Generation-bound dense embedding adapters for canonical ServiceEpisode text.

The adapter never downloads or selects a model.  Runtime composition must inject
one provider with a complete :class:`EmbeddingProfile`.  The local hash provider
is deliberately named and gated as a baseline so it cannot be mistaken for a
production semantic model.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from enum import Enum
from typing import Protocol

from application.hybrid_retrieval import (
    EmbeddingProfile,
    EmbeddingProviderKind,
    RetrievalCorpus,
    RetrievalGeneration,
)
from core.local_embedding import LocalHashEmbeddingFunction


class ServiceEpisodeEmbeddingFailure(str, Enum):
    PROFILE_INCOMPLETE = "SERVICE_EPISODE_EMBEDDING_PROFILE_INCOMPLETE"
    PROFILE_MISMATCH = "SERVICE_EPISODE_EMBEDDING_PROFILE_MISMATCH"
    BASELINE_FORBIDDEN = "SERVICE_EPISODE_HASH_BASELINE_FORBIDDEN"
    INPUT_INVALID = "SERVICE_EPISODE_EMBEDDING_INPUT_INVALID"
    PROVIDER_OUTPUT_INVALID = "SERVICE_EPISODE_EMBEDDING_PROVIDER_OUTPUT_INVALID"
    PROVIDER_UNAVAILABLE = "SERVICE_EPISODE_EMBEDDING_PROVIDER_UNAVAILABLE"


class ServiceEpisodeEmbeddingContractError(ValueError):
    """A deterministic provider/generation/input contract violation."""

    def __init__(self, code: ServiceEpisodeEmbeddingFailure, message: str):
        super().__init__(message)
        self.code = code


class ServiceEpisodeEmbeddingUnavailable(RuntimeError):
    """The declared provider failed before a valid vector was produced."""

    code = ServiceEpisodeEmbeddingFailure.PROVIDER_UNAVAILABLE


class ServiceEpisodeEmbeddingProvider(Protocol):
    """Model adapter seam; providers own their true model/preprocessing identity."""

    profile: EmbeddingProfile

    def embed_documents(
        self, raw_episode_texts: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...

    def embed_queries(
        self, raw_queries: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...


class LocalHashServiceEpisodeEmbeddingBaseline:
    """Dependency-free test/demo baseline; never a learned dense provider."""

    profile = EmbeddingProfile(
        provider="dialogpilot-local-hash-service-episode-baseline-v1",
        provider_kind=EmbeddingProviderKind.HASH_BASELINE,
        model=LocalHashEmbeddingFunction.model_id,
        model_version="1",
        dimension=LocalHashEmbeddingFunction.dimension,
        model_digest=hashlib.sha256(
            b"dialogpilot-hash-embedding-v1:ascii-cjk-unigram-bigram:384d"
        ).hexdigest(),
        document_preprocessing=(
            "raw-service-episode-canonical-text-to-nfkc-lower-"
            "ascii-word-cjk-unigram-bigram-v1"
        ),
        query_preprocessing=(
            "raw-query-to-nfkc-lower-ascii-word-cjk-unigram-bigram-v1"
        ),
    )

    def __init__(self, embedding_function=None):
        self._embedding_function = (
            embedding_function or LocalHashEmbeddingFunction()
        )

    def embed_documents(
        self, raw_episode_texts: Sequence[str],
    ) -> Sequence[Sequence[float]]:
        return self._embedding_function(list(raw_episode_texts))

    def embed_queries(
        self, raw_queries: Sequence[str],
    ) -> Sequence[Sequence[float]]:
        return self._embedding_function(list(raw_queries))


class ServiceEpisodeDocumentEmbedder:
    """Embed raw canonical episode text against one immutable generation."""

    def __init__(
        self,
        provider: ServiceEpisodeEmbeddingProvider,
        *,
        allow_hash_baseline: bool = False,
    ):
        self.provider = provider
        self._profile = _require_provider_profile(provider)
        self._allow_hash_baseline = allow_hash_baseline

    def __call__(
        self,
        raw_episode_texts: Sequence[str],
        generation_profile: EmbeddingProfile,
    ) -> tuple[tuple[float, ...], ...]:
        texts = _validate_raw_text_batch(raw_episode_texts, kind="document")
        _validate_expected_profile(
            self._profile,
            generation_profile,
            allow_hash_baseline=self._allow_hash_baseline,
        )
        try:
            vectors = self.provider.embed_documents(texts)
        except (ServiceEpisodeEmbeddingContractError, ServiceEpisodeEmbeddingUnavailable):
            raise
        except Exception as exc:
            raise ServiceEpisodeEmbeddingUnavailable(
                "ServiceEpisode document embedding provider is unavailable"
            ) from exc
        return _validated_vectors(vectors, len(texts), self._profile)


class ServiceEpisodeQueryEmbedder:
    """Embed raw queries under the same profile used by document projection."""

    def __init__(
        self,
        provider: ServiceEpisodeEmbeddingProvider,
        *,
        allow_hash_baseline: bool = False,
    ):
        self.provider = provider
        self._profile = _require_provider_profile(provider)
        self._allow_hash_baseline = allow_hash_baseline

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def __call__(
        self, raw_query: str, generation: RetrievalGeneration,
    ) -> tuple[float, ...]:
        texts = _validate_raw_text_batch((raw_query,), kind="query")
        if (
            generation.corpus is not RetrievalCorpus.SERVICE_EPISODE
            or not generation.embedding_metadata_complete
        ):
            raise ServiceEpisodeEmbeddingContractError(
                ServiceEpisodeEmbeddingFailure.PROFILE_INCOMPLETE,
                "ServiceEpisode generation requires complete embedding metadata",
            )
        _validate_expected_profile(
            self._profile,
            generation.embedding_profile,
            allow_hash_baseline=self._allow_hash_baseline,
        )
        try:
            vectors = self.provider.embed_queries(texts)
        except (ServiceEpisodeEmbeddingContractError, ServiceEpisodeEmbeddingUnavailable):
            raise
        except Exception as exc:
            raise ServiceEpisodeEmbeddingUnavailable(
                "ServiceEpisode query embedding provider is unavailable"
            ) from exc
        return _validated_vectors(vectors, 1, self._profile)[0]


def _require_provider_profile(
    provider: ServiceEpisodeEmbeddingProvider,
) -> EmbeddingProfile:
    profile = getattr(provider, "profile", None)
    if not isinstance(profile, EmbeddingProfile) or not profile.is_complete:
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.PROFILE_INCOMPLETE,
            "ServiceEpisode provider requires complete embedding metadata",
        )
    return profile


def _validate_expected_profile(
    profile: EmbeddingProfile,
    generation_profile: EmbeddingProfile,
    *,
    allow_hash_baseline: bool,
) -> None:
    if not generation_profile.is_complete or not profile.is_complete:
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.PROFILE_INCOMPLETE,
            "ServiceEpisode generation requires complete embedding metadata",
        )
    if profile != generation_profile:
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.PROFILE_MISMATCH,
            "ServiceEpisode embedding profile differs from generation",
        )
    if profile.is_baseline and not allow_hash_baseline:
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.BASELINE_FORBIDDEN,
            "ServiceEpisode hash embedding is baseline-only",
        )


def _validate_raw_text_batch(
    values: Sequence[str], *, kind: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.INPUT_INVALID,
            f"ServiceEpisode {kind} embedding requires a text batch",
        )
    texts = tuple(values)
    if not texts or any(
        not isinstance(item, str) or not item.strip() for item in texts
    ):
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.INPUT_INVALID,
            f"ServiceEpisode {kind} embedding requires non-blank raw text",
        )
    return texts


def _validated_vectors(
    values: Sequence[Sequence[float]],
    expected_count: int,
    profile: EmbeddingProfile,
) -> tuple[tuple[float, ...], ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.PROVIDER_OUTPUT_INVALID,
            "ServiceEpisode embedding provider returned an invalid batch",
        )
    try:
        vectors = tuple(
            tuple(float(value) for value in vector) for vector in values
        )
    except (TypeError, ValueError) as exc:
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.PROVIDER_OUTPUT_INVALID,
            "ServiceEpisode embedding provider returned non-numeric vectors",
        ) from exc
    if len(vectors) != expected_count or any(
        len(vector) != profile.dimension
        or any(not math.isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise ServiceEpisodeEmbeddingContractError(
            ServiceEpisodeEmbeddingFailure.PROVIDER_OUTPUT_INVALID,
            "ServiceEpisode embedding provider returned invalid dimensions",
        )
    return vectors
