"""Explicit offline activation of retrieval generations used by evaluation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from application.hybrid_retrieval import (
    EmbeddingProfile,
    GenerationConflict,
    RetrievalGeneration,
)


CorpusSelection = Literal["knowledge", "service_episode", "all"]


class KnowledgeGenerationOwner(Protocol):
    def active_generation(self) -> RetrievalGeneration: ...

    async def ensure_defaults_async(self) -> RetrievalGeneration: ...

    async def doc_count_async(self) -> int: ...


class ServiceEpisodeGenerationOwner(Protocol):
    def active_generation(self) -> RetrievalGeneration: ...

    def rebuild_and_activate(
        self, generation_id: str,
    ) -> ServiceEpisodeGenerationBuildView: ...


class ServiceEpisodeGenerationBuildView(Protocol):
    generation: RetrievalGeneration
    episode_count: int


@dataclass(frozen=True)
class GenerationActivation:
    corpus: str
    old: RetrievalGeneration | None
    new: RetrievalGeneration
    count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "corpus": self.corpus,
            "old": _generation_dict(self.old),
            "new": _generation_dict(self.new),
            "count": self.count,
        }


async def rebuild_selected_generations(
    corpus: CorpusSelection,
    *,
    knowledge: KnowledgeGenerationOwner | None = None,
    service_episode: ServiceEpisodeGenerationOwner | None = None,
    service_episode_generation_id: str | None = None,
) -> tuple[GenerationActivation, ...]:
    """Run only the requested owner operations and return their transitions."""
    results: list[GenerationActivation] = []
    if corpus in {"knowledge", "all"}:
        if knowledge is None:
            raise ValueError("Knowledge generation owner is required")
        old = _active_or_none(knowledge)
        new = await knowledge.ensure_defaults_async()
        results.append(GenerationActivation(
            corpus="KNOWLEDGE",
            old=old,
            new=new,
            count=await knowledge.doc_count_async(),
        ))
    if corpus in {"service_episode", "all"}:
        if service_episode is None:
            raise ValueError("ServiceEpisode generation owner is required")
        generation_id = str(service_episode_generation_id or "").strip()
        if not generation_id:
            raise ValueError("ServiceEpisode generation ID is required")
        old = _active_or_none(service_episode)
        build = service_episode.rebuild_and_activate(generation_id)
        results.append(GenerationActivation(
            corpus="SERVICE_EPISODE",
            old=old,
            new=build.generation,
            count=build.episode_count,
        ))
    if not results:
        raise ValueError(f"unsupported retrieval corpus: {corpus}")
    return tuple(results)


def _active_or_none(owner) -> RetrievalGeneration | None:
    try:
        return owner.active_generation()
    except GenerationConflict:
        return None


def _generation_dict(
    generation: RetrievalGeneration | None,
) -> dict[str, object] | None:
    if generation is None:
        return None
    return {
        "generation_id": generation.generation_id,
        "state": generation.state.value,
        "profile": _profile_dict(generation.embedding_profile),
    }


def _profile_dict(profile: EmbeddingProfile) -> dict[str, object]:
    return {
        "fingerprint": profile.fingerprint,
        "provider": profile.provider,
        "provider_kind": profile.provider_kind.value,
        "model": profile.model,
        "model_version": profile.model_version,
        "dimension": profile.dimension,
        "model_digest": profile.model_digest,
        "document_preprocessing": profile.document_preprocessing,
        "query_preprocessing": profile.query_preprocessing,
    }
