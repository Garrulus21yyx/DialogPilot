"""On-demand ServiceEpisode search owner for the direct-cutover tool boundary."""
from __future__ import annotations

from typing import Callable
import unicodedata

from application.hybrid_retrieval import (
    EpisodeSearchScope,
    GenerationState,
    HybridRetrievalRequest,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalResult,
    ServiceEpisodeRetriever,
)


class ServiceEpisodeMemorySearch:
    """Build one request exclusively from authenticated scope and pinned owners."""

    def __init__(
        self,
        *,
        bindings,
        generations,
        retriever: ServiceEpisodeRetriever,
        embed_query: Callable[[str, RetrievalGeneration], tuple[float, ...] | None],
    ):
        self._bindings = bindings
        self._generations = generations
        self._retriever = retriever
        self._embed_query = embed_query

    def search(
        self,
        *,
        tenant_id: str,
        user_id: str,
        query: str,
        entity_ids: tuple[str, ...] = (),
        top_k: int = 5,
    ) -> ServiceEpisodeRetrievalResult:
        query_text = self._normalize_query(query)
        if not tenant_id.strip() or not user_id.strip() or not query_text:
            return _invalid("SEARCH_SCOPE_INCOMPLETE")
        binding = self._bindings.get(tenant_id)
        if binding is None or not binding.enabled:
            return _invalid("DIRECT_CUTOVER_DISABLED")
        target = binding.target
        if target.policy_fingerprint != self._retriever.policy.fingerprint:
            return _conflict("POLICY_BINDING_DRIFT")
        try:
            generation = self._generations.get(target.backend_generation)
        except Exception:
            return ServiceEpisodeRetrievalResult(
                RetrievalStatus.UNAVAILABLE, detail_code="GENERATION_UNAVAILABLE",
            )
        if (
            generation.corpus is not RetrievalCorpus.SERVICE_EPISODE
            or generation.backend_id != target.backend_id
            or generation.source_watermark != target.corpus_generation
            or generation.state not in {GenerationState.READY, GenerationState.ACTIVE}
        ):
            return _conflict("GENERATION_BINDING_DRIFT")
        try:
            embedding = self._embed_query(query_text, generation)
        except ValueError:
            return _invalid("QUERY_EMBEDDING_CONTRACT")
        except Exception:
            embedding = None
        request = HybridRetrievalRequest(
            tenant_id=tenant_id,
            corpus=RetrievalCorpus.SERVICE_EPISODE,
            backend_fingerprint=generation.backend_fingerprint,
            generation_id=generation.generation_id,
            policy_fingerprint=target.policy_fingerprint,
            query_text=query_text,
            query_embedding=embedding,
            scope=EpisodeSearchScope(user_id, entity_ids),
            lexical_limit=self._retriever.policy.fusion.lexical_pool,
        )
        return self._retriever.retrieve(request, top_k=top_k)

    @staticmethod
    def _normalize_query(query: str) -> str:
        return "".join(
            character for character in str(query)
            if unicodedata.category(character) != "Cf"
        ).strip()


def _invalid(detail: str) -> ServiceEpisodeRetrievalResult:
    return ServiceEpisodeRetrievalResult(
        RetrievalStatus.INVALID_CONTRACT, detail_code=detail,
    )


def _conflict(detail: str) -> ServiceEpisodeRetrievalResult:
    return ServiceEpisodeRetrievalResult(RetrievalStatus.CONFLICT, detail_code=detail)
