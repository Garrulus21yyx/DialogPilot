"""Legacy and PostgreSQL implementations of the hybrid candidate port."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

import psycopg
from psycopg import sql

from application.chinese_lexical import postgres_websearch_or_query
from application.hybrid_retrieval import (
    EpisodeSearchScope,
    GenerationState,
    HybridRetrievalRequest,
    HybridRetrievalResult,
    KnowledgeSearchScope,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from infrastructure.postgres import PostgresUnavailableError
from infrastructure.retrieval_postgres import RetrievalPostgresPool


@dataclass(frozen=True)
class LegacyRawCandidate:
    candidate_id: str
    source_id: str
    source_revision: str
    score: float
    provenance_sha256: str


class LegacyCandidateSource(Protocol):
    """Chroma + SQLite/Python BM25 boundary, before application-side fusion."""

    def dense_candidates(
        self, request: HybridRetrievalRequest,
    ) -> Sequence[LegacyRawCandidate]: ...

    def lexical_candidates(
        self, request: HybridRetrievalRequest,
    ) -> Sequence[LegacyRawCandidate]: ...


class LegacySourceInvalidContract(ValueError):
    pass


class ChromaBm25KnowledgeCandidateSource:
    """Raw legacy Knowledge candidates from Chroma and PersistentBM25Index.

    A frozen comparison corpus must carry the new source revision and ACL metadata.
    Older deployed collections fail closed instead of inventing those references.
    """

    def __init__(
        self,
        *,
        collection: Any,
        sparse_index: Any,
        tenant_id: str,
    ):
        self.collection = collection
        self.sparse_index = sparse_index
        self.tenant_id = tenant_id

    def dense_candidates(
        self, request: HybridRetrievalRequest,
    ) -> Sequence[LegacyRawCandidate]:
        scope = self._scope(request)
        if request.query_embedding is None or request.dense_limit < 1:
            return ()
        result = self.collection.query(
            query_embeddings=[list(request.query_embedding)],
            n_results=request.dense_limit,
            where=self._where(scope),
            include=["metadatas", "distances"],
        )
        ids = _first_nested(result.get("ids"))
        metadatas = _first_nested(result.get("metadatas"))
        distances = _first_nested(result.get("distances"))
        if not (len(ids) == len(metadatas) == len(distances)):
            raise LegacySourceInvalidContract("Chroma candidate shape is incomplete")
        return tuple(
            self._candidate(
                candidate_id=str(candidate_id), metadata=metadata,
                score=1.0 - float(distance), scope=scope,
            )
            for candidate_id, metadata, distance in zip(
                ids, metadatas, distances, strict=True,
            )
        )

    def lexical_candidates(
        self, request: HybridRetrievalRequest,
    ) -> Sequence[LegacyRawCandidate]:
        scope = self._scope(request)
        corpus_size = int(self.collection.count())
        ids = self.sparse_index.search(request.query_text, top_k=corpus_size)
        if not ids:
            return ()
        result = self.collection.get(ids=list(ids), include=["metadatas"])
        returned_ids = list(result.get("ids") or [])
        metadatas = list(result.get("metadatas") or [])
        if len(returned_ids) != len(metadatas):
            raise LegacySourceInvalidContract("BM25 metadata shape is incomplete")
        by_id = dict(zip(returned_ids, metadatas, strict=True))
        candidates = []
        for source_rank, candidate_id in enumerate(ids, 1):
            metadata = by_id.get(candidate_id)
            if metadata is None or not self._matches(metadata, scope):
                continue
            candidates.append(self._candidate(
                candidate_id=str(candidate_id), metadata=metadata,
                score=1.0 / source_rank, scope=scope,
            ))
            if len(candidates) >= request.lexical_limit:
                break
        return tuple(candidates)

    def _scope(self, request: HybridRetrievalRequest) -> KnowledgeSearchScope:
        if request.corpus is not RetrievalCorpus.KNOWLEDGE or not isinstance(
            request.scope, KnowledgeSearchScope,
        ):
            raise LegacySourceInvalidContract("legacy Knowledge source corpus mismatch")
        if request.tenant_id != self.tenant_id:
            raise LegacySourceInvalidContract("legacy corpus tenant mismatch")
        return request.scope

    @staticmethod
    def _where(scope: KnowledgeSearchScope) -> dict[str, object]:
        filters: list[dict[str, object]] = [
            {"scope": scope.scope}, {"locale": scope.locale},
        ]
        if scope.product is not None:
            filters.append({"product": scope.product})
        return {"$and": filters}

    @staticmethod
    def _matches(metadata: object, scope: KnowledgeSearchScope) -> bool:
        if not isinstance(metadata, dict):
            return False
        return (
            metadata.get("scope") == scope.scope
            and metadata.get("locale") == scope.locale
            and (scope.product is None or metadata.get("product") == scope.product)
        )

    @staticmethod
    def _candidate(
        *, candidate_id: str, metadata: object, score: float,
        scope: KnowledgeSearchScope,
    ) -> LegacyRawCandidate:
        if not isinstance(metadata, dict) or not ChromaBm25KnowledgeCandidateSource._matches(
            metadata, scope,
        ):
            raise LegacySourceInvalidContract("legacy candidate ACL metadata mismatch")
        source_id = str(metadata.get("source_id") or "").strip()
        source_revision = str(metadata.get("source_revision") or "").strip()
        if not source_id or not source_revision:
            raise LegacySourceInvalidContract(
                "legacy candidate lacks canonical source revision"
            )
        provenance = str(metadata.get("provenance_sha256") or "")
        if len(provenance) != 64:
            raise LegacySourceInvalidContract(
                "legacy candidate lacks canonical provenance"
            )
        return LegacyRawCandidate(
            candidate_id, source_id, source_revision, float(score), provenance,
        )


class LegacyHybridBackend:
    """Contract adapter over corpus-specific legacy Chroma/BM25 sources."""

    def __init__(
        self,
        *,
        generations: Mapping[str, RetrievalGeneration],
        sources: Mapping[RetrievalCorpus, LegacyCandidateSource],
    ):
        self.generations = dict(generations)
        self.sources = dict(sources)

    def retrieve(self, request: HybridRetrievalRequest) -> HybridRetrievalResult:
        generation = self.generations.get(request.generation_id)
        if generation is None or generation.corpus is not request.corpus:
            return _empty(request, RetrievalStatus.INVALID_CONTRACT, "GENERATION_UNKNOWN")
        if generation.backend_fingerprint != request.backend_fingerprint:
            return _empty(request, RetrievalStatus.CONFLICT, "BACKEND_FINGERPRINT_DRIFT")
        source = self.sources.get(request.corpus)
        if source is None:
            return _empty(request, RetrievalStatus.UNAVAILABLE, "LEGACY_SOURCE_UNAVAILABLE")
        try:
            dense = (
                source.dense_candidates(request)[:request.dense_limit]
                if request.query_embedding is not None and request.dense_limit else ()
            )
            lexical = (
                source.lexical_candidates(request)[:request.lexical_limit]
                if request.query_text.strip() and request.lexical_limit else ()
            )
        except LegacySourceInvalidContract:
            return _empty(request, RetrievalStatus.INVALID_CONTRACT, "LEGACY_SOURCE_CONTRACT")
        except Exception:
            return _empty(request, RetrievalStatus.UNAVAILABLE, "LEGACY_BACKEND_ERROR")
        dense_candidates = _rank_legacy(dense, request, generation)
        lexical_candidates = _rank_legacy(lexical, request, generation)
        return HybridRetrievalResult(
            status=(
                RetrievalStatus.OK
                if dense_candidates or lexical_candidates
                else RetrievalStatus.NO_EVIDENCE
            ),
            backend_fingerprint=request.backend_fingerprint,
            generation_id=request.generation_id,
            dense_candidates=dense_candidates,
            lexical_candidates=lexical_candidates,
            index_watermark=generation.source_watermark,
        )


class PostgresHybridBackend:
    """pgvector cosine and PG_FTS_ZH_V1 candidate generation."""

    readable_states = {
        GenerationState.READY.value,
        GenerationState.ACTIVE.value,
        GenerationState.RETIRED.value,
    }

    def __init__(self, pool: RetrievalPostgresPool):
        self.pool = pool

    def retrieve(self, request: HybridRetrievalRequest) -> HybridRetrievalResult:
        try:
            with self.pool.transaction() as connection:
                row = connection.execute("""
                    SELECT backend_fingerprint, embedding_dimension, state,
                           distance_metric, chinese_tokenizer, lexical_ranker,
                           source_watermark
                    FROM retrieval_generation_registry
                    WHERE corpus=%s AND backend_id IS NOT NULL AND generation_id=%s
                """, (request.corpus.value, request.generation_id)).fetchone()
                if row is None:
                    return _empty(
                        request, RetrievalStatus.INVALID_CONTRACT,
                        "GENERATION_UNKNOWN",
                    )
                if row[0] != request.backend_fingerprint:
                    return _empty(
                        request, RetrievalStatus.CONFLICT,
                        "BACKEND_FINGERPRINT_DRIFT",
                    )
                if row[2] not in self.readable_states:
                    return _empty(
                        request, RetrievalStatus.INVALID_CONTRACT,
                        "GENERATION_NOT_READABLE",
                    )
                if row[3] != "COSINE":
                    return _empty(
                        request, RetrievalStatus.INVALID_CONTRACT,
                        "DISTANCE_METRIC_UNSUPPORTED",
                    )
                if row[4] != "ascii-cjk-unigram-bigram-v1" or row[5] != "PG_FTS_ZH_V1":
                    return _empty(
                        request, RetrievalStatus.INVALID_CONTRACT,
                        "LEXICAL_CONTRACT_UNSUPPORTED",
                    )
                dimension = int(row[1])
                if request.query_embedding is not None and len(
                    request.query_embedding
                ) != dimension:
                    return _empty(
                        request, RetrievalStatus.INVALID_CONTRACT,
                        "QUERY_EMBEDDING_DIMENSION_MISMATCH",
                    )
                dense = self._dense(connection, request, dimension)
                lexical = self._lexical(connection, request)
        except PostgresUnavailableError:
            return _empty(request, RetrievalStatus.UNAVAILABLE, "POSTGRES_UNAVAILABLE")
        except (
            psycopg.errors.UndefinedTable,
            psycopg.errors.UndefinedColumn,
            psycopg.errors.UndefinedFunction,
            psycopg.DataError,
        ):
            return _empty(request, RetrievalStatus.INVALID_CONTRACT, "POSTGRES_SCHEMA_CONTRACT")
        except psycopg.Error:
            return _empty(request, RetrievalStatus.UNAVAILABLE, "POSTGRES_UNAVAILABLE")
        return HybridRetrievalResult(
            status=(
                RetrievalStatus.OK if dense or lexical
                else RetrievalStatus.NO_EVIDENCE
            ),
            backend_fingerprint=request.backend_fingerprint,
            generation_id=request.generation_id,
            dense_candidates=dense,
            lexical_candidates=lexical,
            index_watermark=str(row[6]),
        )

    def _dense(
        self,
        connection,
        request: HybridRetrievalRequest,
        dimension: int,
    ) -> tuple[RetrievalCandidate, ...]:
        if request.query_embedding is None or request.dense_limit == 0:
            return ()
        if request.exact_dense:
            connection.execute("SET LOCAL enable_indexscan=off")
            connection.execute("SET LOCAL enable_bitmapscan=off")
        embedding = "[" + ",".join(format(value, ".17g") for value in request.query_embedding) + "]"
        (
            table, source_column, revision_column, freshness_column,
            filters, params,
        ) = _scope_sql(request)
        query = sql.SQL("""
            SELECT candidate_id, {source_column}, {revision_column}, provenance_sha256,
                   1 - (embedding::vector({dimension}) <=> %s::vector({dimension})) AS score,
                   {freshness_column}
            FROM retrieval.{table}
            WHERE tenant_id=%s AND generation_id=%s AND {filters}
              AND embedding IS NOT NULL
            ORDER BY embedding::vector({dimension}) <=> %s::vector({dimension}),
                     candidate_id
            LIMIT %s
        """).format(
            dimension=sql.Literal(dimension), table=sql.Identifier(table),
            source_column=sql.Identifier(source_column),
            revision_column=sql.Identifier(revision_column),
            freshness_column=sql.Identifier(freshness_column), filters=filters,
        )
        rows = connection.execute(query, (
            embedding, request.tenant_id, request.generation_id,
            *params, embedding, request.dense_limit,
        )).fetchall()
        return _rows_to_candidates(rows, request)

    def _lexical(
        self, connection, request: HybridRetrievalRequest,
    ) -> tuple[RetrievalCandidate, ...]:
        if not request.query_text.strip() or request.lexical_limit == 0:
            return ()
        lexical_query = postgres_websearch_or_query(request.query_text)
        if not lexical_query:
            return ()
        (
            table, source_column, revision_column, freshness_column,
            filters, params,
        ) = _scope_sql(request)
        query = sql.SQL("""
            WITH query AS (
                SELECT websearch_to_tsquery('simple', %s) AS value
            )
            SELECT candidate_id, {source_column}, {revision_column}, provenance_sha256,
                   ts_rank_cd(search_tsv, query.value) AS score,
                   {freshness_column}
            FROM retrieval.{table}, query
            WHERE tenant_id=%s AND generation_id=%s AND {filters}
              AND search_tsv @@ query.value
            ORDER BY score DESC, candidate_id
            LIMIT %s
        """).format(
            table=sql.Identifier(table),
            source_column=sql.Identifier(source_column),
            revision_column=sql.Identifier(revision_column),
            freshness_column=sql.Identifier(freshness_column), filters=filters,
        )
        rows = connection.execute(query, (
            lexical_query, request.tenant_id, request.generation_id,
            *params, request.lexical_limit,
        )).fetchall()
        return _rows_to_candidates(rows, request)


def hnsw_index_name(generation: RetrievalGeneration) -> str:
    suffix = hashlib.sha256(generation.generation_id.encode("utf-8")).hexdigest()[:16]
    corpus = "knowledge" if generation.corpus is RetrievalCorpus.KNOWLEDGE else "episode"
    return f"{corpus}_hnsw_{suffix}"


def create_generation_hnsw_index(connection, generation: RetrievalGeneration) -> str:
    """Build an immutable, generation-scoped cosine HNSW index."""
    if generation.index_method != "HNSW" or generation.distance_metric.value != "COSINE":
        raise ValueError("generation does not declare cosine HNSW")
    params = json.loads(generation.index_params_json)
    m = int(params.get("m", 16))
    ef_construction = int(params.get("ef_construction", 64))
    if not 2 <= m <= 100 or not 4 <= ef_construction <= 1000:
        raise ValueError("HNSW parameters are outside the supported budget")
    table = (
        "knowledge_chunk_search"
        if generation.corpus is RetrievalCorpus.KNOWLEDGE
        else "service_episode_search"
    )
    name = hnsw_index_name(generation)
    existing = connection.execute("""
        SELECT am.amname, pg_get_indexdef(index_class.oid),
               COALESCE(index_class.reloptions, ARRAY[]::text[])
        FROM pg_class index_class
        JOIN pg_namespace namespace ON namespace.oid=index_class.relnamespace
        JOIN pg_am am ON am.oid=index_class.relam
        WHERE namespace.nspname='retrieval' AND index_class.relname=%s
    """, (name,)).fetchone()
    if existing is not None:
        definition = str(existing[1])
        options = set(existing[2])
        required_fragments = (
            f"vector({generation.embedding_dimension})",
            "vector_cosine_ops",
            generation.generation_id,
            "embedding IS NOT NULL",
        )
        if (
            existing[0] != "hnsw"
            or not all(fragment in definition for fragment in required_fragments)
            or options != {f"m={m}", f"ef_construction={ef_construction}"}
        ):
            raise ValueError(
                "existing generation HNSW index contract drift: "
                f"definition={definition!r}, options={sorted(options)!r}"
            )
        return name
    query = sql.SQL("""
        CREATE INDEX {name} ON retrieval.{table}
        USING hnsw ((embedding::vector({dimension})) vector_cosine_ops)
        WITH (m={m}, ef_construction={ef_construction})
        WHERE generation_id={generation_id} AND embedding IS NOT NULL
    """).format(
        name=sql.Identifier(name), table=sql.Identifier(table),
        dimension=sql.Literal(generation.embedding_dimension),
        m=sql.Literal(m), ef_construction=sql.Literal(ef_construction),
        generation_id=sql.Literal(generation.generation_id),
    )
    connection.execute(query)
    return name


def _scope_sql(request: HybridRetrievalRequest):
    if request.corpus is RetrievalCorpus.KNOWLEDGE:
        scope = request.scope
        assert isinstance(scope, KnowledgeSearchScope)
        filters = sql.SQL("scope=%s AND locale=%s")
        params: list[object] = [scope.scope, scope.locale]
        if scope.product is not None:
            filters += sql.SQL(" AND product=%s")
            params.append(scope.product)
        return (
            "knowledge_chunk_search", "source_id", "source_revision",
            "projected_at",
            filters, params,
        )
    scope = request.scope
    assert isinstance(scope, EpisodeSearchScope)
    filters = sql.SQL("user_id=%s")
    params = [scope.user_id]
    if scope.entity_ids:
        filters += sql.SQL(" AND entity_ids && %s::text[]")
        params.append(list(scope.entity_ids))
    return (
        "service_episode_search", "episode_id", "episode_revision",
        "verified_at",
        filters, params,
    )


def _rows_to_candidates(rows, request):
    return tuple(
        RetrievalCandidate(
            candidate_id=str(row[0]), corpus=request.corpus,
            generation_id=request.generation_id, source_id=str(row[1]),
            source_revision=str(row[2]), rank=rank, score=float(row[4]),
            provenance_sha256=str(row[3]),
            freshness_at=row[5].isoformat(),
        )
        for rank, row in enumerate(rows, 1)
    )


def _rank_legacy(candidates, request, generation):
    return tuple(
        RetrievalCandidate(
            candidate_id=item.candidate_id, corpus=generation.corpus,
            generation_id=generation.generation_id, source_id=item.source_id,
            source_revision=item.source_revision, rank=rank, score=item.score,
            provenance_sha256=item.provenance_sha256,
        )
        for rank, item in enumerate(candidates, 1)
    )


def _empty(request, status, detail):
    return HybridRetrievalResult(
        status=status, backend_fingerprint=request.backend_fingerprint,
        generation_id=request.generation_id, detail_code=detail,
    )


def _first_nested(value: object) -> list[object]:
    if not isinstance(value, list) or not value:
        return []
    first = value[0]
    return list(first) if isinstance(first, list) else []
