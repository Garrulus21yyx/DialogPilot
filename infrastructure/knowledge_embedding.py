"""Generation-bound embedding adapters for PostgreSQL Knowledge retrieval.

The provider owns model and preprocessing identity.  Ingestion supplies raw
source chunks to ``embed_documents`` and retrieval supplies raw queries to
``embed_queries``; PostgreSQL lexical tokenization is a separate projection.
"""
from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Protocol

from application.hybrid_retrieval import (
    EmbeddingProfile,
    EmbeddingProviderKind,
    RetrievalCorpus,
    RetrievalGeneration,
)
from core.local_embedding import LocalHashEmbeddingFunction


class KnowledgeEmbeddingContractError(ValueError):
    pass


class KnowledgeEmbeddingProvider(Protocol):
    profile: EmbeddingProfile

    def embed_documents(
        self, raw_source_chunks: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...

    def embed_queries(
        self, raw_queries: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...


class LocalHashKnowledgeEmbeddingBaseline:
    """Dependency-free baseline/test provider; never a learned dense model."""

    profile = EmbeddingProfile(
        provider="dialogpilot-local-hash-baseline-provider-v1",
        provider_kind=EmbeddingProviderKind.HASH_BASELINE,
        model=LocalHashEmbeddingFunction.model_id,
        model_version="1",
        dimension=LocalHashEmbeddingFunction.dimension,
        model_digest=hashlib.sha256(
            b"dialogpilot-hash-embedding-v1:ascii-cjk-unigram-bigram:384d"
        ).hexdigest(),
        document_preprocessing=(
            "raw-text-to-nfkc-lower-ascii-word-cjk-unigram-bigram-v1"
        ),
        query_preprocessing=(
            "raw-text-to-nfkc-lower-ascii-word-cjk-unigram-bigram-v1"
        ),
    )

    def __init__(self, embedding_function=None):
        self._embedding_function = (
            embedding_function or LocalHashEmbeddingFunction()
        )

    def embed_documents(
        self, raw_source_chunks: Sequence[str],
    ) -> Sequence[Sequence[float]]:
        return self._embedding_function(list(raw_source_chunks))

    def embed_queries(
        self, raw_queries: Sequence[str],
    ) -> Sequence[Sequence[float]]:
        return self._embedding_function(list(raw_queries))


class KnowledgeDocumentEmbedder:
    """Validate provider output for a batch of raw source chunk strings."""

    def __init__(self, provider: KnowledgeEmbeddingProvider):
        _require_profile(provider)
        self.provider = provider

    def __call__(
        self, raw_source_chunks: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]:
        texts = _validate_raw_text_batch(raw_source_chunks, kind="document")
        try:
            vectors = self.provider.embed_documents(texts)
        except KnowledgeEmbeddingContractError:
            raise
        except Exception as exc:
            raise KnowledgeEmbeddingContractError(
                "knowledge document embedding provider failed"
            ) from exc
        return _validated_vectors(vectors, len(texts), self.provider.profile)


class KnowledgeQueryEmbedder:
    """Embed a raw query only when the provider matches the pinned generation."""

    def __init__(self, provider: KnowledgeEmbeddingProvider):
        _require_profile(provider)
        self.provider = provider

    def __call__(
        self, raw_query: str, generation: RetrievalGeneration,
    ) -> tuple[float, ...]:
        texts = _validate_raw_text_batch((raw_query,), kind="query")
        if (
            generation.corpus is not RetrievalCorpus.KNOWLEDGE
            or not generation.embedding_metadata_complete
        ):
            raise KnowledgeEmbeddingContractError(
                "knowledge query requires a complete Knowledge generation profile"
            )
        if not self.provider.profile.matches_generation(generation):
            raise KnowledgeEmbeddingContractError(
                "knowledge query embedding profile differs from generation"
            )
        try:
            vectors = self.provider.embed_queries(texts)
        except KnowledgeEmbeddingContractError:
            raise
        except Exception as exc:
            raise KnowledgeEmbeddingContractError(
                "knowledge query embedding provider failed"
            ) from exc
        return _validated_vectors(
            vectors, 1, self.provider.profile,
        )[0]


def _validate_raw_text_batch(
    values: Sequence[str], *, kind: str,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise KnowledgeEmbeddingContractError(
            f"knowledge {kind} embedding requires a text batch"
        )
    texts = tuple(values)
    if not texts or any(not isinstance(item, str) or not item.strip() for item in texts):
        raise KnowledgeEmbeddingContractError(
            f"knowledge {kind} embedding requires non-blank raw text"
        )
    return texts


def _require_profile(provider: KnowledgeEmbeddingProvider) -> EmbeddingProfile:
    profile = getattr(provider, "profile", None)
    if not isinstance(profile, EmbeddingProfile) or not profile.is_complete:
        raise KnowledgeEmbeddingContractError(
            "knowledge embedding provider requires complete versioned metadata"
        )
    return profile


def _validated_vectors(
    values: Sequence[Sequence[float]],
    expected_count: int,
    profile: EmbeddingProfile,
) -> tuple[tuple[float, ...], ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise KnowledgeEmbeddingContractError(
            "knowledge embedding provider returned an invalid batch"
        )
    try:
        vectors = tuple(
            tuple(float(value) for value in vector) for vector in values
        )
    except (TypeError, ValueError) as exc:
        raise KnowledgeEmbeddingContractError(
            "knowledge embedding provider returned non-numeric vectors"
        ) from exc
    if len(vectors) != expected_count or any(
        len(vector) != profile.dimension
        or any(not math.isfinite(value) for value in vector)
        for vector in vectors
    ):
        raise KnowledgeEmbeddingContractError(
            "knowledge embedding provider returned invalid vector dimensions"
        )
    return vectors
