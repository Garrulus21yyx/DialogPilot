"""Build one immutable ServiceEpisode retrieval generation from canonical heads."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from application.chinese_lexical import TOKENIZER_VERSION
from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    GenerationConflict,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.hybrid_retrieval_backend import create_generation_hnsw_index
from infrastructure.retrieval_postgres import PostgresRetrievalGenerationRegistry


@dataclass(frozen=True)
class ServiceEpisodeGenerationBuild:
    generation: RetrievalGeneration
    episode_count: int


@dataclass(frozen=True)
class _CurrentEpisode:
    tenant_id: str
    user_id: str
    conversation_id: str
    deletion_epoch: int
    episode_id: str
    revision: int
    provenance_sha256: str


class PostgresServiceEpisodeGenerationManager:
    """Replay current canonical episodes before atomically changing the pointer."""

    backend_id = "POSTGRES_PG_FTS_ZH_V1"
    backend_fingerprint = "POSTGRES_PGVECTOR_PG_FTS_ZH_V1"
    schema_version = "service-episode-search-v1"
    index_params_json = '{"ef_construction":64,"m":16}'

    def __init__(self, pool, *, projector, embedding_profile: EmbeddingProfile):
        self._pool = pool
        self._projector = projector
        self._embedding_profile = embedding_profile
        self._generations = PostgresRetrievalGenerationRegistry(pool)

    def rebuild_and_activate(
        self, generation_id: str,
    ) -> ServiceEpisodeGenerationBuild:
        if not generation_id.strip():
            raise ValueError("ServiceEpisode generation ID is required")
        snapshot = self._current_snapshot()
        manifest_hash = _snapshot_hash(snapshot)
        definition = self._generation(generation_id, manifest_hash)
        try:
            generation = self._generations.get(generation_id)
        except GenerationConflict:
            generation = self._generations.register(definition)
        if generation.immutable_fingerprint() != definition.immutable_fingerprint():
            raise GenerationConflict("ServiceEpisode generation identity is immutable")
        if generation.state is GenerationState.REGISTERED:
            generation = self._generations.transition(
                generation_id, GenerationState.BUILDING,
            )
        if generation.state is GenerationState.ACTIVE:
            return ServiceEpisodeGenerationBuild(generation, len(snapshot))
        if generation.state not in {GenerationState.BUILDING, GenerationState.READY}:
            raise GenerationConflict(
                f"ServiceEpisode generation is not buildable: {generation.state.value}"
            )
        if generation.state is GenerationState.BUILDING:
            event_ids = self._enqueue(snapshot, generation)
            for event_id in event_ids:
                result = self._projector.project(event_id)
                if result.code.value not in {"APPLIED", "ALREADY_APPLIED"}:
                    self._generations.transition(
                        generation_id, GenerationState.FAILED,
                    )
                    raise GenerationConflict(
                        f"ServiceEpisode projection failed: {result.code.value}"
                    )
            if self._current_snapshot() != snapshot:
                self._generations.transition(
                    generation_id, GenerationState.FAILED,
                )
                raise GenerationConflict(
                    "canonical ServiceEpisode snapshot changed during generation build"
                )
            with self._pool.transaction() as connection:
                projected = int(connection.execute("""
                    SELECT count(*) FROM retrieval.service_episode_search
                    WHERE backend_id=%s AND generation_id=%s
                """, (self.backend_id, generation_id)).fetchone()[0])
                if projected != len(snapshot):
                    raise GenerationConflict(
                        "ServiceEpisode projection count differs from canonical snapshot"
                    )
                create_generation_hnsw_index(connection, generation)
            generation = self._generations.transition(
                generation_id, GenerationState.READY,
            )
        generation = self._generations.activate_direct(generation_id)
        return ServiceEpisodeGenerationBuild(generation, len(snapshot))

    def _generation(
        self, generation_id: str, manifest_hash: str,
    ) -> RetrievalGeneration:
        with self._pool.transaction() as connection:
            vector_version = connection.execute("""
                SELECT extversion FROM pg_extension WHERE extname='vector'
            """).fetchone()
        if vector_version is None:
            raise RuntimeError("pgvector extension is unavailable")
        profile = self._embedding_profile
        return RetrievalGeneration(
            generation_id=generation_id,
            corpus=RetrievalCorpus.SERVICE_EPISODE,
            backend_id=self.backend_id,
            backend_fingerprint=self.backend_fingerprint,
            schema_version=self.schema_version,
            source_watermark=manifest_hash,
            embedding_model=profile.model,
            embedding_dimension=profile.dimension,
            embedding_model_digest=profile.model_digest,
            distance_metric=DistanceMetric.COSINE,
            vector_extension_version=str(vector_version[0]),
            index_method="HNSW",
            index_params_json=self.index_params_json,
            chinese_tokenizer=TOKENIZER_VERSION,
            lexical_ranker="PG_FTS_ZH_V1",
            manifest_hash=manifest_hash,
            embedding_provider=profile.provider,
            embedding_provider_kind=profile.provider_kind,
            embedding_model_version=profile.model_version,
            embedding_document_preprocessing=profile.document_preprocessing,
            embedding_query_preprocessing=profile.query_preprocessing,
        )

    def _current_snapshot(self) -> tuple[_CurrentEpisode, ...]:
        with self._pool.transaction() as connection:
            rows = connection.execute("""
                SELECT revision.tenant_id, revision.user_id,
                       revision.conversation_id, revision.source_deletion_epoch,
                       revision.episode_id, revision.revision,
                       revision.provenance_sha256
                FROM dialogpilot_app.service_episode_heads head
                JOIN dialogpilot_app.service_episode_revisions revision
                  ON revision.episode_id=head.episode_id
                 AND revision.revision=head.current_revision
                JOIN dialogpilot_app.conversations conversation
                  ON conversation.tenant_id=revision.tenant_id
                 AND conversation.user_id=revision.user_id
                 AND conversation.conversation_id=revision.conversation_id
                WHERE conversation.deleted_at IS NULL
                  AND conversation.deletion_epoch=revision.source_deletion_epoch
                ORDER BY revision.tenant_id, revision.user_id,
                         revision.conversation_id, revision.episode_id
            """).fetchall()
        return tuple(_CurrentEpisode(*row) for row in rows)

    def _enqueue(
        self,
        snapshot: tuple[_CurrentEpisode, ...],
        generation: RetrievalGeneration,
    ) -> tuple[str, ...]:
        event_ids = tuple(
            _event_id(generation.generation_id, item.episode_id, item.revision)
            for item in snapshot
        )
        with self._pool.transaction() as connection:
            for event_id, item in zip(event_ids, snapshot, strict=True):
                connection.execute("""
                    INSERT INTO retrieval.canonical_projection_outbox (
                        event_id,corpus,tenant_id,backend_id,generation_id,
                        source_ref,source_revision,source_fingerprint,
                        subject_user_id,subject_conversation_id,deletion_epoch
                    ) VALUES (
                        %s,'SERVICE_EPISODE',%s,%s,%s,%s,%s,%s,%s,%s,%s
                    ) ON CONFLICT (event_id) DO NOTHING
                """, (
                    event_id, item.tenant_id, generation.backend_id,
                    generation.generation_id, item.episode_id,
                    str(item.revision), item.provenance_sha256, item.user_id,
                    item.conversation_id, item.deletion_epoch,
                ))
        return event_ids


def _snapshot_hash(snapshot: tuple[_CurrentEpisode, ...]) -> str:
    payload = [item.__dict__ for item in snapshot]
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def _event_id(generation_id: str, episode_id: str, revision: int) -> str:
    identity = (
        f"service-episode-generation\x1f{generation_id}"
        f"\x1f{episode_id}\x1f{revision}"
    )
    return hashlib.sha256(
        identity.encode()
    ).hexdigest()
