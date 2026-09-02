"""Dedicated PostgreSQL pool for retrieval reads and projection writes."""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import psycopg
from psycopg import Connection, sql
from psycopg_pool import ConnectionPool

from application.hybrid_retrieval import (
    DistanceMetric,
    GenerationConflict,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.postgres import PostgresUnavailableError
from infrastructure.postgres import PostgresPool


@dataclass(frozen=True)
class RetrievalPoolConfig:
    database_url: str
    min_size: int = 1
    max_size: int = 4
    pool_timeout_seconds: float = 2.0
    statement_timeout_ms: int = 750
    role: str = "dialogpilot_retrieval"

    def __post_init__(self) -> None:
        if not self.database_url.strip():
            raise ValueError("RETRIEVAL_DATABASE_URL is required")
        if self.min_size < 0 or self.max_size < 1 or self.min_size > self.max_size:
            raise ValueError("invalid retrieval pool bounds")
        if self.pool_timeout_seconds <= 0 or self.statement_timeout_ms < 1:
            raise ValueError("retrieval timeouts must be positive")
        if self.role != "dialogpilot_retrieval":
            raise ValueError("retrieval pool must assume the bounded retrieval role")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "RetrievalPoolConfig":
        values = env or os.environ
        return cls(
            database_url=str(values.get("RETRIEVAL_DATABASE_URL") or ""),
            min_size=int(values.get("RETRIEVAL_POOL_MIN_SIZE", "1")),
            max_size=int(values.get("RETRIEVAL_POOL_MAX_SIZE", "4")),
            pool_timeout_seconds=float(
                values.get("RETRIEVAL_POOL_TIMEOUT_SECONDS", "2")
            ),
            statement_timeout_ms=int(
                values.get("RETRIEVAL_STATEMENT_TIMEOUT_MS", "750")
            ),
        )


@dataclass(frozen=True)
class RetrievalPoolMetrics:
    transactions_started: int
    transactions_succeeded: int
    transactions_failed: int
    unavailable_failures: int


class RetrievalPostgresPool:
    """A resource budget isolated from the OLTP ``PostgresPool``."""

    def __init__(self, config: RetrievalPoolConfig):
        self.config = config
        self._lock = threading.Lock()
        self._started = 0
        self._succeeded = 0
        self._failed = 0
        self._unavailable = 0
        self._pool = ConnectionPool(
            conninfo=config.database_url,
            min_size=config.min_size,
            max_size=config.max_size,
            timeout=config.pool_timeout_seconds,
            open=False,
            kwargs={"autocommit": False},
            configure=self._configure,
            name="dialogpilot-retrieval",
        )

    def _configure(self, connection: Connection) -> None:
        connection.execute(sql.SQL("SET ROLE {}").format(
            sql.Identifier(self.config.role),
        ))
        connection.execute(
            "SELECT set_config('statement_timeout', %s, false)",
            (str(self.config.statement_timeout_ms),),
        )
        connection.execute("SET search_path TO retrieval, public")
        connection.execute(
            "SELECT set_config('application_name', 'dialogpilot-retrieval', false)"
        )
        connection.commit()

    def open(self) -> None:
        try:
            self._pool.open(wait=True, timeout=self.config.pool_timeout_seconds)
            with self._pool.connection() as connection:
                row = connection.execute("""
                    SELECT current_user,
                           current_setting('statement_timeout'),
                           to_regnamespace('retrieval') IS NOT NULL,
                           extversion
                    FROM pg_extension WHERE extname='vector'
                """).fetchone()
                if row is None or row[0] != self.config.role or row[2] is not True:
                    raise PostgresUnavailableError(
                        "retrieval role/schema/vector extension is unavailable"
                    )
        except PostgresUnavailableError:
            self._pool.close()
            raise
        except (psycopg.Error, TimeoutError) as exc:
            self._pool.close()
            raise PostgresUnavailableError("retrieval PostgreSQL is unavailable") from exc

    @contextmanager
    def transaction(self) -> Iterator[Connection]:
        self._increment("_started")
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    yield connection
            self._increment("_succeeded")
        except psycopg.OperationalError as exc:
            self._increment("_failed")
            if exc.sqlstate is None or exc.sqlstate.startswith("08") or exc.sqlstate in {
                "57P01", "57P02", "57P03",
            }:
                self._increment("_unavailable")
                raise PostgresUnavailableError(
                    "retrieval PostgreSQL transaction failed"
                ) from exc
            raise
        except Exception:
            self._increment("_failed")
            raise

    def metrics(self) -> RetrievalPoolMetrics:
        with self._lock:
            return RetrievalPoolMetrics(
                self._started, self._succeeded, self._failed, self._unavailable,
            )

    def close(self) -> None:
        self._pool.close()

    def _increment(self, field: str) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)


class PostgresRetrievalGenerationRegistry:
    """Platform-owned immutable generation registry and atomic pointer CAS."""

    def __init__(self, platform_pool: PostgresPool):
        self.pool = platform_pool

    def register(self, generation: RetrievalGeneration) -> RetrievalGeneration:
        with self.pool.transaction() as connection:
            row = connection.execute("""
                INSERT INTO retrieval.retrieval_generation_registry (
                    corpus, backend_id, generation_id, backend_fingerprint,
                    immutable_fingerprint, schema_version, source_watermark,
                    embedding_model, embedding_dimension, embedding_model_digest,
                    distance_metric, vector_extension_version, index_method,
                    index_params, chinese_tokenizer, lexical_ranker, manifest_hash,
                    state
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb, %s, %s, %s, %s
                )
                ON CONFLICT (generation_id) DO NOTHING
                RETURNING generation_id
            """, (
                generation.corpus.value, generation.backend_id,
                generation.generation_id, generation.backend_fingerprint,
                generation.immutable_fingerprint(), generation.schema_version,
                generation.source_watermark, generation.embedding_model,
                generation.embedding_dimension, generation.embedding_model_digest,
                generation.distance_metric.value, generation.vector_extension_version,
                generation.index_method, generation.index_params_json,
                generation.chinese_tokenizer, generation.lexical_ranker,
                generation.manifest_hash, generation.state.value,
            )).fetchone()
            existing = self._get(connection, generation.generation_id, for_update=True)
        if row is None and existing != generation:
            raise GenerationConflict("generation identity is immutable")
        return existing

    def get(self, generation_id: str) -> RetrievalGeneration:
        """Return the authoritative generation, including its current lifecycle state."""
        with self.pool.transaction() as connection:
            return self._get(connection, generation_id, for_update=False)

    def active(
        self, corpus: RetrievalCorpus, *, backend_id: str,
    ) -> RetrievalGeneration:
        """Resolve the single active generation without a rollout pointer."""
        with self.pool.transaction() as connection:
            rows = connection.execute("""
                SELECT generation_id
                FROM retrieval.retrieval_generation_registry
                WHERE corpus=%s AND backend_id=%s AND state='ACTIVE'
                ORDER BY generation_id
            """, (corpus.value, backend_id)).fetchall()
            if len(rows) != 1:
                raise GenerationConflict(
                    "exactly one active retrieval generation is required"
                )
            return self._get(connection, str(rows[0][0]), for_update=False)

    def activate_direct(self, generation_id: str) -> RetrievalGeneration:
        """Atomically replace the local active generation; no previous pointer."""
        with self.pool.transaction() as connection:
            target = self._get(connection, generation_id, for_update=True)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{target.corpus.value}:{target.backend_id}",),
            )
            target = self._get(connection, generation_id, for_update=True)
            if target.state is GenerationState.ACTIVE:
                return target
            if target.state is not GenerationState.READY:
                raise GenerationConflict("only READY generation can become active")
            connection.execute("""
                UPDATE retrieval.retrieval_generation_registry
                SET state='RETIRED'
                WHERE corpus=%s AND backend_id=%s AND state='ACTIVE'
            """, (target.corpus.value, target.backend_id))
            connection.execute("""
                UPDATE retrieval.retrieval_generation_registry
                SET state='ACTIVE' WHERE generation_id=%s
            """, (generation_id,))
            return RetrievalGeneration(**{
                **target.__dict__, "state": GenerationState.ACTIVE,
            })

    def transition(
        self, generation_id: str, target: GenerationState,
    ) -> RetrievalGeneration:
        with self.pool.transaction() as connection:
            current = self._get(connection, generation_id, for_update=True)
            try:
                row = connection.execute("""
                    UPDATE retrieval.retrieval_generation_registry
                    SET state=%s WHERE generation_id=%s
                    RETURNING generation_id
                """, (target.value, generation_id)).fetchone()
            except psycopg.errors.ObjectNotInPrerequisiteState as exc:
                raise GenerationConflict(str(exc)) from exc
            if row is None:
                raise GenerationConflict("generation disappeared")
            return RetrievalGeneration(**{**current.__dict__, "state": target})

    @staticmethod
    def _get(
        connection: Connection, generation_id: str, *, for_update: bool,
    ) -> RetrievalGeneration:
        row = connection.execute("""
            SELECT generation_id, corpus, backend_id, backend_fingerprint,
                   schema_version, source_watermark, embedding_model,
                   embedding_dimension, embedding_model_digest, distance_metric,
                   vector_extension_version, index_method,
                   index_params, chinese_tokenizer, lexical_ranker,
                   manifest_hash, state
            FROM retrieval.retrieval_generation_registry
            WHERE generation_id=%s
        """ + (" FOR UPDATE" if for_update else ""), (generation_id,)).fetchone()
        if row is None:
            raise GenerationConflict("unknown generation")
        return RetrievalGeneration(
            generation_id=row[0], corpus=RetrievalCorpus(row[1]),
            backend_id=row[2], backend_fingerprint=row[3], schema_version=row[4],
            source_watermark=row[5], embedding_model=row[6],
            embedding_dimension=int(row[7]), embedding_model_digest=row[8],
            distance_metric=DistanceMetric(row[9]), vector_extension_version=row[10],
            index_method=row[11], index_params_json=json.dumps(
                row[12], sort_keys=True, separators=(",", ":"),
            ),
            chinese_tokenizer=row[13], lexical_ranker=row[14], manifest_hash=row[15],
            state=GenerationState(row[16]),
        )
