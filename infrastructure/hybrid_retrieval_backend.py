"""PostgreSQL implementation of the hybrid candidate port."""
from __future__ import annotations
import time

import hashlib
import json
import psycopg
from psycopg import sql

from application.chinese_lexical import (
    postgres_lexical_document,
    postgres_websearch_or_query,
)
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

class PostgresHybridBackend:
    """pgvector cosine plus versioned PostgreSQL lexical candidate generation."""

    readable_states = {
        GenerationState.READY.value,
        GenerationState.ACTIVE.value,
        GenerationState.RETIRED.value,
    }

    def __init__(self, pool: RetrievalPostgresPool):
        self.pool = pool

    def retrieve(self, request: HybridRetrievalRequest, *, deadline: float | None = None) -> HybridRetrievalResult:
        try:
            with self.pool.transaction() as connection:
                if deadline is not None:
                    remaining_ms = int((deadline-time.monotonic())*1000)
                    if remaining_ms <= 0:
                        return _empty(request, RetrievalStatus.UNAVAILABLE, "RETRIEVAL_DEADLINE_EXCEEDED")
                    connection.execute("SELECT set_config('statement_timeout', %s, true)",
                                       (str(min(remaining_ms, self.pool.config.statement_timeout_ms)),))
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
                if row[4] != "ascii-cjk-unigram-bigram-v1" or row[5] not in {
                    "PG_FTS_ZH_V1", "PG_BM25_ZH_V1",
                }:
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
                if deadline is not None:
                    remaining_ms = int((deadline-time.monotonic())*1000)
                    if remaining_ms <= 0:
                        return _empty(request, RetrievalStatus.UNAVAILABLE, "RETRIEVAL_DEADLINE_EXCEEDED")
                    connection.execute("SELECT set_config('statement_timeout', %s, true)",
                                       (str(min(remaining_ms, self.pool.config.statement_timeout_ms)),))
                dense = self._dense(connection, request, dimension)
                lexical = self._lexical(connection, request, str(row[5]))
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
        self, connection, request: HybridRetrievalRequest, lexical_ranker: str,
    ) -> tuple[RetrievalCandidate, ...]:
        if not request.query_text.strip() or request.lexical_limit == 0:
            return ()
        if lexical_ranker == "PG_BM25_ZH_V1":
            return self._bm25(connection, request)
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

    def _bm25(
        self, connection, request: HybridRetrievalRequest,
    ) -> tuple[RetrievalCandidate, ...]:
        """Rank the scoped Knowledge corpus with Robertson BM25 (k1=1.2,b=.75)."""
        if request.corpus is not RetrievalCorpus.KNOWLEDGE:
            return ()
        query_terms = tuple(dict.fromkeys(
            postgres_lexical_document(request.query_text).split()
        ))
        if not query_terms:
            return ()
        (
            table, source_column, revision_column, freshness_column,
            filters, params,
        ) = _scope_sql(request)
        query = sql.SQL("""
            WITH query_terms AS (
                SELECT token FROM unnest(%s::text[]) AS token
            ),
            scoped AS MATERIALIZED (
                SELECT candidate_id, {source_column}, {revision_column},
                       provenance_sha256, {freshness_column}, lexical_terms,
                       cardinality(lexical_terms)::double precision AS dl
                FROM retrieval.{table}
                WHERE tenant_id=%s AND generation_id=%s AND {filters}
            ),
            stats AS (
                SELECT count(*)::double precision AS n,
                       GREATEST(avg(dl), 1.0) AS avgdl
                FROM scoped
            ),
            term_stats AS (
                SELECT q.token, count(*)::double precision AS df
                FROM query_terms q
                JOIN scoped d ON q.token = ANY(d.lexical_terms)
                GROUP BY q.token
            ),
            term_frequency AS (
                SELECT d.candidate_id, d.{source_column}, d.{revision_column},
                       d.provenance_sha256, d.{freshness_column}, d.dl,
                       q.token, count(*)::double precision AS tf
                FROM scoped d
                JOIN query_terms q ON q.token = ANY(d.lexical_terms)
                CROSS JOIN LATERAL unnest(d.lexical_terms) AS terms(value)
                WHERE terms.value = q.token
                GROUP BY d.candidate_id, d.{source_column}, d.{revision_column},
                         d.provenance_sha256, d.{freshness_column}, d.dl, q.token
            )
            SELECT tf.candidate_id, tf.{source_column}, tf.{revision_column},
                   tf.provenance_sha256,
                   sum(
                       ln(1.0 + (stats.n - ts.df + 0.5) / (ts.df + 0.5))
                       * (tf.tf * 2.2)
                       / (tf.tf + 1.2 * (0.25 + 0.75 * tf.dl / stats.avgdl))
                   ) AS score,
                   tf.{freshness_column}
            FROM term_frequency tf
            JOIN term_stats ts USING (token)
            CROSS JOIN stats
            GROUP BY tf.candidate_id, tf.{source_column}, tf.{revision_column},
                     tf.provenance_sha256, tf.{freshness_column}
            ORDER BY score DESC, tf.candidate_id
            LIMIT %s
        """).format(
            table=sql.Identifier(table),
            source_column=sql.Identifier(source_column),
            revision_column=sql.Identifier(revision_column),
            freshness_column=sql.Identifier(freshness_column),
            filters=filters,
        )
        rows = connection.execute(query, (
            list(query_terms), request.tenant_id, request.generation_id,
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
        if scope.source_types:
            filters += sql.SQL(" AND source_type=ANY(%s::text[])")
            params.append(list(scope.source_types))
        if scope.as_of is not None:
            from infrastructure.knowledge_applicability import source_applicability
            applicability, values = source_applicability(
                "knowledge_chunk_search", as_of=scope.as_of, region=scope.applicable_region,
                channel=scope.applicable_channel, product=scope.applicable_product)
            filters += sql.SQL(" AND ") + applicability
            params.extend(values)
        if scope.regions:
            filters += sql.SQL(" AND region=ANY(%s::text[])")
            params.append(list(scope.regions))
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


def _empty(request, status, detail):
    return HybridRetrievalResult(
        status=status, backend_fingerprint=request.backend_fingerprint,
        generation_id=request.generation_id, detail_code=detail,
    )
