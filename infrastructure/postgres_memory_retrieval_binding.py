"""PostgreSQL CAS owner for the single direct-cutover Memory target."""
from __future__ import annotations

from application.hybrid_retrieval import GenerationConflict
from application.memory_retrieval_policy import (
    MemoryRetrievalBinding,
    MemoryRetrievalTarget,
)


class PostgresMemoryRetrievalBindingRepository:
    corpus = "SERVICE_EPISODE"

    def __init__(self, pool):
        self.pool = pool

    def get(self, tenant_id: str) -> MemoryRetrievalBinding | None:
        if not tenant_id.strip():
            raise ValueError("memory retrieval binding tenant is required")
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT policy_fingerprint,backend_id,backend_generation,
                       corpus_generation,enabled,version
                FROM retrieval.memory_retrieval_bindings
                WHERE tenant_id=%s AND corpus=%s
            """, (tenant_id, self.corpus)).fetchone()
        return _binding(row) if row else None

    def initialize(
        self, tenant_id: str, *, target: MemoryRetrievalTarget,
    ) -> MemoryRetrievalBinding:
        _require_tenant(tenant_id)
        binding = MemoryRetrievalBinding(target, False, 1)
        with self.pool.transaction() as connection:
            inserted = connection.execute("""
                INSERT INTO retrieval.memory_retrieval_bindings (
                    tenant_id,corpus,policy_fingerprint,backend_id,
                    backend_generation,corpus_generation,enabled,version
                ) VALUES (%s,%s,%s,%s,%s,%s,FALSE,1)
                ON CONFLICT (tenant_id,corpus) DO NOTHING
                RETURNING version
            """, (
                tenant_id, self.corpus, target.policy_fingerprint,
                target.backend_id, target.backend_generation,
                target.corpus_generation,
            )).fetchone()
            if inserted is None:
                row = connection.execute("""
                    SELECT policy_fingerprint,backend_id,backend_generation,
                           corpus_generation,enabled,version
                    FROM retrieval.memory_retrieval_bindings
                    WHERE tenant_id=%s AND corpus=%s FOR UPDATE
                """, (tenant_id, self.corpus)).fetchone()
                if _binding(row) != binding:
                    raise GenerationConflict("memory retrieval binding already differs")
        return binding

    def replace_before_cutover(
        self,
        tenant_id: str,
        target: MemoryRetrievalTarget,
        *,
        expected_version: int,
    ) -> MemoryRetrievalBinding:
        _require_tenant(tenant_id)
        updated = MemoryRetrievalBinding(target, False, expected_version + 1)
        with self.pool.transaction() as connection:
            write = connection.execute("""
                UPDATE retrieval.memory_retrieval_bindings SET
                    policy_fingerprint=%s,backend_id=%s,backend_generation=%s,
                    corpus_generation=%s,version=%s,
                    updated_at=transaction_timestamp()
                WHERE tenant_id=%s AND corpus=%s AND version=%s
                  AND enabled=FALSE
            """, (
                target.policy_fingerprint, target.backend_id,
                target.backend_generation, target.corpus_generation,
                updated.version, tenant_id, self.corpus, expected_version,
            ))
            if write.rowcount != 1:
                raise GenerationConflict("memory retrieval binding CAS conflict")
        return updated

    def activate(
        self, tenant_id: str, *, expected_version: int,
    ) -> MemoryRetrievalBinding:
        _require_tenant(tenant_id)
        with self.pool.transaction() as connection:
            row = connection.execute("""
                UPDATE retrieval.memory_retrieval_bindings SET
                    enabled=TRUE,version=version+1,
                    updated_at=transaction_timestamp()
                WHERE tenant_id=%s AND corpus=%s AND version=%s
                  AND enabled=FALSE
                RETURNING policy_fingerprint,backend_id,backend_generation,
                          corpus_generation,enabled,version
            """, (tenant_id, self.corpus, expected_version)).fetchone()
        if row is None:
            raise GenerationConflict("memory retrieval activation CAS conflict")
        return _binding(row)


def _binding(row) -> MemoryRetrievalBinding:
    return MemoryRetrievalBinding(
        MemoryRetrievalTarget(str(row[0]), str(row[1]), str(row[2]), str(row[3])),
        bool(row[4]), int(row[5]),
    )


def _require_tenant(tenant_id: str) -> None:
    if not tenant_id.strip():
        raise ValueError("memory retrieval binding tenant is required")
