"""Evaluation-only retrievers for the LoCoMo conversation-session slice."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Protocol

from application.hybrid_retrieval import EmbeddingProfile
from evaluation.command_primary_eval.locomo_session_contracts import (
    BenchmarkSessionDocument,
    RankedSessionHit,
)
from infrastructure.bge_m3_embedding import (
    BGE_M3_MODEL_ID,
    BGEM3EmbeddingConfig,
    LocalBGEM3EmbeddingProvider,
)


RRF_K = 60
RRF_LEXICAL_WEIGHT = 0.60
RRF_DENSE_WEIGHT = 0.30
RRF_SELECTION_STATUS = "FIXED_DEV_DIAGNOSTIC_NOT_SELECTED"


class SessionEmbeddingProvider(Protocol):
    profile: EmbeddingProfile

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...

    def embed_queries(
        self,
        texts: Sequence[str],
    ) -> Sequence[Sequence[float]]: ...


class TokenOverlapSessionRetriever:
    """Deterministic lexical diagnostic over complete session documents."""

    version = "token-overlap-session-baseline-v1"
    _token = re.compile(r"[a-z0-9]+")

    @property
    def descriptor(self) -> Mapping[str, object]:
        return {
            "kind": "token_overlap",
            "embedding_profile": None,
            "fusion": None,
        }

    def retrieve(
        self,
        *,
        query: str,
        documents: Sequence[BenchmarkSessionDocument],
        top_k: int,
    ) -> Sequence[RankedSessionHit]:
        query_terms = Counter(self._token.findall(query.casefold()))
        ranked = []
        for document in documents:
            terms = Counter(self._token.findall(document.content.casefold()))
            score = float(
                sum(
                    min(count, terms.get(term, 0))
                    for term, count in query_terms.items()
                )
            )
            ranked.append(RankedSessionHit(document.session_id, score))
        return _top_hits(ranked, top_k)


class DenseCosineSessionRetriever:
    """Embed raw sessions and questions through one versioned model profile."""

    version = "bge-m3-session-cosine-v1"

    def __init__(self, provider: SessionEmbeddingProvider):
        if not isinstance(provider.profile, EmbeddingProfile):
            raise ValueError("session embedding provider profile is invalid")
        self._provider = provider
        self._document_cache_key: tuple[tuple[str, str], ...] | None = None
        self._document_vectors: tuple[tuple[float, ...], ...] = ()

    @property
    def descriptor(self) -> Mapping[str, object]:
        return {
            "kind": (
                "bge_m3_cosine"
                if self._provider.profile.model == BGE_M3_MODEL_ID
                else "dense_cosine"
            ),
            "embedding_profile": _profile_descriptor(self._provider.profile),
            "similarity": "cosine",
            "fusion": None,
        }

    def retrieve(
        self,
        *,
        query: str,
        documents: Sequence[BenchmarkSessionDocument],
        top_k: int,
    ) -> Sequence[RankedSessionHit]:
        materialized = tuple(documents)
        cache_key = tuple((item.session_id, item.content) for item in materialized)
        if cache_key != self._document_cache_key:
            self._document_vectors = _validated_vectors(
                self._provider.embed_documents(
                    tuple(item.content for item in materialized)
                ),
                expected_count=len(materialized),
                dimension=self._provider.profile.dimension,
            )
            self._document_cache_key = cache_key
        query_vector = _validated_vectors(
            self._provider.embed_queries((query,)),
            expected_count=1,
            dimension=self._provider.profile.dimension,
        )[0]
        ranked = tuple(
            RankedSessionHit(document.session_id, _cosine(query_vector, vector))
            for document, vector in zip(
                materialized,
                self._document_vectors,
                strict=True,
            )
        )
        return _top_hits(ranked, top_k)


class LexicalDenseRRFSessionRetriever:
    """Fixed lexical+dense RRF diagnostic; it is not a production policy."""

    version = "locomo-session-lexical-dense-rrf-v1"

    def __init__(
        self,
        dense: DenseCosineSessionRetriever,
        lexical: TokenOverlapSessionRetriever | None = None,
    ):
        self._dense = dense
        self._lexical = lexical or TokenOverlapSessionRetriever()

    @property
    def descriptor(self) -> Mapping[str, object]:
        return {
            "kind": "lexical_dense_rrf",
            "embedding_profile": self._dense.descriptor["embedding_profile"],
            "fusion": {
                "method": "weighted_rrf",
                "rrf_k": RRF_K,
                "weights": {
                    "lexical": RRF_LEXICAL_WEIGHT,
                    "dense": RRF_DENSE_WEIGHT,
                    "recency": None,
                },
                "selection_status": RRF_SELECTION_STATUS,
            },
        }

    def retrieve(
        self,
        *,
        query: str,
        documents: Sequence[BenchmarkSessionDocument],
        top_k: int,
    ) -> Sequence[RankedSessionHit]:
        candidate_count = len(documents)
        lexical = self._lexical.retrieve(
            query=query,
            documents=documents,
            top_k=candidate_count,
        )
        dense = self._dense.retrieve(
            query=query,
            documents=documents,
            top_k=candidate_count,
        )
        scores: dict[str, float] = {}
        for hits, weight in (
            (lexical, RRF_LEXICAL_WEIGHT),
            (dense, RRF_DENSE_WEIGHT),
        ):
            for rank, hit in enumerate(hits, start=1):
                scores[hit.session_id] = scores.get(hit.session_id, 0.0) + (
                    weight / (RRF_K + rank)
                )
        return _top_hits(
            tuple(
                RankedSessionHit(session_id, score)
                for session_id, score in scores.items()
            ),
            top_k,
        )


def build_bge_m3_session_retriever(
    config: BGEM3EmbeddingConfig,
) -> DenseCosineSessionRetriever:
    return DenseCosineSessionRetriever(LocalBGEM3EmbeddingProvider(config))


def build_rrf_session_retriever(
    config: BGEM3EmbeddingConfig,
) -> LexicalDenseRRFSessionRetriever:
    return LexicalDenseRRFSessionRetriever(build_bge_m3_session_retriever(config))


def _validated_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    dimension: int,
) -> tuple[tuple[float, ...], ...]:
    materialized = tuple(tuple(float(value) for value in vector) for vector in vectors)
    if len(materialized) != expected_count or any(
        len(vector) != dimension or any(not math.isfinite(value) for value in vector)
        for vector in materialized
    ):
        raise ValueError("session embedding provider returned invalid vectors")
    return materialized


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        raise ValueError("cosine similarity requires non-zero vectors")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _profile_descriptor(profile: EmbeddingProfile) -> Mapping[str, object]:
    return {
        "provider": profile.provider,
        "provider_kind": profile.provider_kind.value,
        "model": profile.model,
        "model_version": profile.model_version,
        "dimension": profile.dimension,
        "model_digest": profile.model_digest,
        "document_preprocessing": profile.document_preprocessing,
        "query_preprocessing": profile.query_preprocessing,
        "fingerprint": profile.fingerprint,
    }


def _top_hits(
    hits: Sequence[RankedSessionHit],
    top_k: int,
) -> tuple[RankedSessionHit, ...]:
    return tuple(
        sorted(
            hits,
            key=lambda item: (-item.score, _session_number(item.session_id)),
        )[:top_k]
    )


def _session_number(session_id: str) -> int:
    return int(session_id.removeprefix("S"))
