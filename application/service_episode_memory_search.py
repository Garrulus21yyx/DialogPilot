"""On-demand ServiceEpisode search over the single active PostgreSQL generation."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Callable
import unicodedata

from application.hybrid_retrieval import (
    EmbeddingProfile,
    EpisodeSearchScope,
    GenerationState,
    HybridRetrievalRequest,
    RetrievalContractError,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalPurpose,
    ServiceEpisodeRetrievalResult,
    ServiceEpisodeRetriever,
)


class ServiceEpisodeMemorySearch:
    """Build one request exclusively from authenticated scope and pinned owners."""

    def __init__(
        self,
        *,
        generations,
        retriever: ServiceEpisodeRetriever | None = None,
        retrievers: Mapping[
            ServiceEpisodeRetrievalPurpose, ServiceEpisodeRetriever
        ] | None = None,
        embed_query: Callable[[str, RetrievalGeneration], tuple[float, ...] | None],
        backend_id: str = "POSTGRES_PG_FTS_ZH_V1",
    ):
        configured = dict(retrievers or {})
        if retriever is not None:
            existing = configured.setdefault(retriever.policy.purpose, retriever)
            if existing is not retriever:
                raise ValueError("duplicate ServiceEpisode purpose policy")
        if not configured or any(
            purpose is not value.policy.purpose
            for purpose, value in configured.items()
        ):
            raise ValueError("ServiceEpisode retrievers must be keyed by purpose")
        self._generations = generations
        self._retrievers = configured
        self._embed_query = embed_query
        self._backend_id = backend_id

    def search(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        entity_ids: tuple[str, ...] = (),
        purpose: ServiceEpisodeRetrievalPurpose | str | None = None,
        explicit_time_reference: bool = False,
        top_k: int = 5,
    ) -> ServiceEpisodeRetrievalResult:
        if (
            not isinstance(query, str)
            or not isinstance(entity_ids, tuple)
            or not isinstance(top_k, int)
            or isinstance(top_k, bool)
        ):
            return _invalid("SEARCH_SCOPE_INCOMPLETE")
        query_text = self._normalize_query(query)
        if (
            not isinstance(tenant_id, str)
            or not tenant_id.strip()
            or not isinstance(user_id, str)
            or not user_id.strip()
            or not query_text
            or not isinstance(explicit_time_reference, bool)
            or top_k < 1
            or any(
                not isinstance(item, str) or not item.strip()
                for item in entity_ids
            )
        ):
            return _invalid("SEARCH_SCOPE_INCOMPLETE")
        try:
            selected_purpose = self._select_purpose(purpose)
        except ValueError as exc:
            return _invalid(str(exc))
        retriever = self._retrievers.get(selected_purpose)
        if retriever is None:
            return _invalid(
                "RETRIEVAL_PURPOSE_NOT_CONFIGURED", purpose=selected_purpose,
            )
        try:
            generation = self._generations.active(
                RetrievalCorpus.SERVICE_EPISODE, backend_id=self._backend_id,
            )
        except Exception:
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.UNAVAILABLE,
                detail_code="GENERATION_UNAVAILABLE",
                purpose=selected_purpose,
                policy_version=retriever.policy.version,
            )
        if (
            generation.corpus is not RetrievalCorpus.SERVICE_EPISODE
            or generation.backend_id != self._backend_id
            or generation.state is not GenerationState.ACTIVE
        ):
            return _conflict(
                "ACTIVE_GENERATION_DRIFT",
                purpose=selected_purpose,
                policy_version=retriever.policy.version,
            )
        try:
            embedding = self._embed_query(query_text, generation)
        except ValueError as exc:
            return _invalid(
                _embedding_failure_code(exc, "QUERY_EMBEDDING_CONTRACT"),
                purpose=selected_purpose,
            )
        except Exception as exc:
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.UNAVAILABLE,
                detail_code=_embedding_failure_code(
                    exc, "QUERY_EMBEDDING_PROVIDER_UNAVAILABLE",
                ),
                purpose=selected_purpose,
                policy_version=retriever.policy.version,
                generation_id=generation.generation_id,
            )
        if embedding is None:
            return _invalid(
                "QUERY_EMBEDDING_MISSING", purpose=selected_purpose,
            )
        try:
            request = HybridRetrievalRequest(
                tenant_id=tenant_id,
                corpus=RetrievalCorpus.SERVICE_EPISODE,
                backend_fingerprint=generation.backend_fingerprint,
                generation_id=generation.generation_id,
                policy_fingerprint=retriever.policy.fingerprint,
                query_text=query_text,
                query_embedding=embedding,
                scope=EpisodeSearchScope(user_id, entity_ids),
                lexical_limit=retriever.policy.fusion.lexical_pool,
            )
        except RetrievalContractError:
            return _invalid(
                "SEARCH_SCOPE_INVALID", purpose=selected_purpose,
            )
        result = retriever.retrieve(
            request,
            purpose=selected_purpose,
            explicit_time_reference=explicit_time_reference,
            top_k=top_k,
        )
        profile = getattr(self._embed_query, "profile", None)
        return replace(
            result,
            generation_id=generation.generation_id,
            embedding_profile_fingerprint=(
                profile.fingerprint
                if isinstance(profile, EmbeddingProfile) else None
            ),
        )

    def _select_purpose(
        self, purpose: ServiceEpisodeRetrievalPurpose | str | None,
    ) -> ServiceEpisodeRetrievalPurpose:
        if purpose is None:
            if len(self._retrievers) != 1:
                raise ValueError("RETRIEVAL_PURPOSE_REQUIRED")
            return next(iter(self._retrievers))
        try:
            return ServiceEpisodeRetrievalPurpose(purpose)
        except ValueError as exc:
            raise ValueError("RETRIEVAL_PURPOSE_UNSUPPORTED") from exc

    @staticmethod
    def _normalize_query(query: str) -> str:
        return "".join(
            character for character in str(query)
            if unicodedata.category(character) != "Cf"
        ).strip()


def _invalid(
    detail: str,
    *,
    purpose: ServiceEpisodeRetrievalPurpose = (
        ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE
    ),
) -> ServiceEpisodeRetrievalResult:
    return ServiceEpisodeRetrievalResult(
        RetrievalStatus.INVALID_CONTRACT, detail_code=detail, purpose=purpose,
    )


def _conflict(
    detail: str,
    *,
    purpose: ServiceEpisodeRetrievalPurpose,
    policy_version: str,
) -> ServiceEpisodeRetrievalResult:
    return ServiceEpisodeRetrievalResult(
        RetrievalStatus.CONFLICT,
        detail_code=detail,
        purpose=purpose,
        policy_version=policy_version,
    )


def _embedding_failure_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "code", None)
    return str(getattr(code, "value", code) or fallback)
