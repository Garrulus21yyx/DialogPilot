"""PostgreSQL CAS owner for the complete Memory retrieval binding tuple."""
from __future__ import annotations

from application.memory_retrieval_policy import (
    MemoryRetrievalBinding,
    MemoryRetrievalConsumerMode,
    MemoryRetrievalTarget,
)
from application.hybrid_retrieval import GenerationConflict


class PostgresMemoryRetrievalBindingRepository:
    corpus = "SERVICE_EPISODE"

    def __init__(self, pool):
        self.pool = pool

    def get(self, tenant_id: str) -> MemoryRetrievalBinding | None:
        if not tenant_id.strip():
            raise ValueError("memory retrieval binding tenant is required")
        with self.pool.transaction() as connection:
            row = connection.execute("""
                SELECT mode,active_policy_fingerprint,active_backend_id,
                       active_backend_generation,active_corpus_generation,
                       previous_policy_fingerprint,previous_backend_id,
                       previous_backend_generation,previous_corpus_generation,
                       candidate_policy_fingerprint,candidate_backend_id,
                       candidate_backend_generation,candidate_corpus_generation,
                       version
                FROM retrieval.memory_retrieval_bindings
                WHERE tenant_id=%s AND corpus=%s
            """, (tenant_id, self.corpus)).fetchone()
        return _binding(row) if row else None

    def initialize_shadow(
        self,
        tenant_id: str,
        *,
        active: MemoryRetrievalTarget,
        candidate: MemoryRetrievalTarget,
    ) -> MemoryRetrievalBinding:
        binding = MemoryRetrievalBinding(
            MemoryRetrievalConsumerMode.SHADOW,
            active=active, previous=active, candidate=candidate, version=1,
        )
        with self.pool.transaction() as connection:
            inserted = connection.execute("""
                INSERT INTO retrieval.memory_retrieval_bindings (
                    tenant_id,corpus,mode,active_policy_fingerprint,
                    active_backend_id,active_backend_generation,
                    active_corpus_generation,previous_policy_fingerprint,
                    previous_backend_id,previous_backend_generation,
                    previous_corpus_generation,candidate_policy_fingerprint,
                    candidate_backend_id,candidate_backend_generation,
                    candidate_corpus_generation,version
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
                ON CONFLICT (tenant_id,corpus) DO NOTHING
                RETURNING version
            """, _values(tenant_id, binding)[:-1]).fetchone()
            if inserted is None:
                existing = connection.execute("""
                    SELECT mode,active_policy_fingerprint,active_backend_id,
                           active_backend_generation,active_corpus_generation,
                           previous_policy_fingerprint,previous_backend_id,
                           previous_backend_generation,previous_corpus_generation,
                           candidate_policy_fingerprint,candidate_backend_id,
                           candidate_backend_generation,candidate_corpus_generation,
                           version
                    FROM retrieval.memory_retrieval_bindings
                    WHERE tenant_id=%s AND corpus=%s FOR UPDATE
                """, (tenant_id, self.corpus)).fetchone()
                if _binding(existing) != binding:
                    raise GenerationConflict("memory retrieval binding already differs")
        return binding

    def compare_and_swap(
        self,
        tenant_id: str,
        binding: MemoryRetrievalBinding,
        *,
        expected_version: int,
    ) -> MemoryRetrievalBinding:
        if binding.version != expected_version + 1:
            raise GenerationConflict("memory retrieval binding version mismatch")
        with self.pool.transaction() as connection:
            write = connection.execute("""
                UPDATE retrieval.memory_retrieval_bindings SET
                    mode=%s,active_policy_fingerprint=%s,active_backend_id=%s,
                    active_backend_generation=%s,active_corpus_generation=%s,
                    previous_policy_fingerprint=%s,previous_backend_id=%s,
                    previous_backend_generation=%s,previous_corpus_generation=%s,
                    candidate_policy_fingerprint=%s,candidate_backend_id=%s,
                    candidate_backend_generation=%s,candidate_corpus_generation=%s,
                    version=%s,updated_at=transaction_timestamp()
                WHERE tenant_id=%s AND corpus=%s AND version=%s
            """, (
                *_update_values(binding), tenant_id, self.corpus, expected_version,
            ))
            if write.rowcount != 1:
                raise GenerationConflict("memory retrieval binding CAS conflict")
        return binding

    def rollback(
        self, tenant_id: str, *, expected_version: int,
    ) -> MemoryRetrievalBinding:
        current = self.get(tenant_id)
        if current is None or current.version != expected_version:
            raise GenerationConflict("memory retrieval rollback version conflict")
        return self.compare_and_swap(
            tenant_id, current.rollback(), expected_version=expected_version,
        )


def _binding(row) -> MemoryRetrievalBinding:
    candidate = (
        MemoryRetrievalTarget(str(row[9]), str(row[10]), str(row[11]), str(row[12]))
        if row[9] is not None else None
    )
    return MemoryRetrievalBinding(
        MemoryRetrievalConsumerMode(str(row[0])),
        MemoryRetrievalTarget(str(row[1]), str(row[2]), str(row[3]), str(row[4])),
        MemoryRetrievalTarget(str(row[5]), str(row[6]), str(row[7]), str(row[8])),
        candidate, int(row[13]),
    )


def _values(tenant_id: str, binding: MemoryRetrievalBinding) -> tuple[object, ...]:
    return (tenant_id, "SERVICE_EPISODE", *_update_values(binding))


def _update_values(binding: MemoryRetrievalBinding) -> tuple[object, ...]:
    candidate = binding.candidate
    return (
        binding.mode.value,
        binding.active.policy_fingerprint, binding.active.backend_id,
        binding.active.backend_generation, binding.active.corpus_generation,
        binding.previous.policy_fingerprint, binding.previous.backend_id,
        binding.previous.backend_generation, binding.previous.corpus_generation,
        candidate.policy_fingerprint if candidate else None,
        candidate.backend_id if candidate else None,
        candidate.backend_generation if candidate else None,
        candidate.corpus_generation if candidate else None,
        binding.version,
    )
