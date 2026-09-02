"""M4-T04 canonical PostgreSQL offline replay through the full target chain."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from application.chinese_lexical import TOKENIZER_VERSION, postgres_lexical_document
from application.hybrid_retrieval import (
    DistanceMetric,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from application.memory_retrieval_policy import (
    LEGACY_MEMORY_RETRIEVAL_POLICY,
    MemoryRetrievalTarget,
)
from application.service_episode_memory_search import ServiceEpisodeMemorySearch
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetriever,
)
from evaluation.service_episode_replay import (
    ServiceEpisodeReplayCase,
    run_service_episode_replay,
)
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_memory_retrieval_binding import (
    PostgresMemoryRetrievalBindingRepository,
)
from infrastructure.retrieval_postgres import (
    PostgresRetrievalGenerationRegistry,
    RetrievalPoolConfig,
    RetrievalPostgresPool,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "c" * 64


@pytest.fixture()
def replay_chain(postgres_database_url):
    PostgresMigrationRunner(postgres_database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
    ))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(
        postgres_database_url, min_size=1, max_size=2,
        statement_timeout_ms=2000,
    ))
    platform.open()
    retrieval.open()
    with platform.transaction() as connection:
        connection.execute("""
            TRUNCATE retrieval.memory_retrieval_bindings,
                     retrieval.service_episode_search,
                     retrieval.retrieval_generation_registry,
                     dialogpilot_app.conversations CASCADE
        """)
    policy = ServiceEpisodeRetrievalPolicy(
        "service-episode-offline-replay-v1", LEGACY_MEMORY_RETRIEVAL_POLICY,
        0.0, 365 * 86400,
    )
    generation = RetrievalGeneration(
        "episode-replay-generation-v1", RetrievalCorpus.SERVICE_EPISODE,
        "POSTGRES_PG_FTS_ZH_V1", "episode-replay-backend-v1",
        "service-episode-v1", "episode-outbox:fixture-4", "embedding-v1", 2,
        SHA, DistanceMetric.COSINE, "0.8.6", "HNSW",
        '{"ef_construction":64,"m":16}', TOKENIZER_VERSION, "PG_FTS_ZH_V1",
        SHA,
    )
    registry = PostgresRetrievalGenerationRegistry(platform)
    registry.register(generation)
    registry.transition(generation.generation_id, GenerationState.BUILDING)
    subjects = (
        ("tenant-replay", "user-replay", "conversation-e401"),
        ("tenant-replay", "user-replay", "conversation-refund"),
        ("tenant-replay", "user-other", "conversation-other-user"),
        ("tenant-other", "user-replay", "conversation-other-tenant"),
    )
    with platform.transaction() as connection:
        for subject in subjects:
            connection.execute("""
                INSERT INTO dialogpilot_app.conversations (
                    tenant_id,user_id,conversation_id
                ) VALUES (%s,%s,%s)
            """, subject)
        rows = (
            ("candidate-e401", "tenant-replay", "user-replay", "conversation-e401", "case-e401", "E401 登录失败 设备验证"),
            ("candidate-refund", "tenant-replay", "user-replay", "conversation-refund", "case-refund", "订单 A123 重复扣款 退款"),
            ("candidate-other-user", "tenant-replay", "user-other", "conversation-other-user", "case-other-user", "E401 登录失败 设备验证"),
            ("candidate-other-tenant", "tenant-other", "user-replay", "conversation-other-tenant", "case-other-tenant", "E401 登录失败 设备验证"),
        )
        for candidate_id, tenant_id, user_id, conversation_id, episode_id, text in rows:
            connection.execute("""
                INSERT INTO retrieval.service_episode_search (
                    candidate_id,tenant_id,user_id,source_conversation_id,
                    backend_id,generation_id,episode_id,episode_revision,
                    outcome_receipt_ref,provenance_sha256,deletion_epoch,
                    verified_at,lexical_document,projected_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,'1',%s,%s,0,
                          '2026-09-01T00:00:00+00:00',%s,
                          '2026-09-01T00:00:00+00:00')
            """, (
                candidate_id, tenant_id, user_id, conversation_id,
                generation.backend_id, generation.generation_id, episode_id,
                f"receipt:{episode_id}", SHA, postgres_lexical_document(text),
            ))
    registry.transition(generation.generation_id, GenerationState.READY)
    bindings = PostgresMemoryRetrievalBindingRepository(platform)
    bindings.initialize("tenant-replay", target=MemoryRetrievalTarget(
        policy.fingerprint, generation.backend_id, generation.generation_id,
        generation.source_watermark,
    ))
    bindings.activate("tenant-replay", expected_version=1)
    search = ServiceEpisodeMemorySearch(
        bindings=bindings, generations=registry,
        retriever=ServiceEpisodeRetriever(PostgresHybridBackend(retrieval), policy),
        embed_query=lambda _query, _generation: None,
    )
    try:
        yield search
    finally:
        retrieval.close()
        platform.close()


def test_canonical_postgres_replay_matches_frozen_privacy_safe_report(replay_chain):
    report = run_service_episode_replay(
        replay_chain,
        (
            ServiceEpisodeReplayCase(
                "similar-e401", "E401 登录失败", ("case-e401",),
                ("case-other-user", "case-other-tenant"),
            ),
            ServiceEpisodeReplayCase(
                "exact-order-refund", "订单 A123 重复扣款", ("case-refund",),
                ("case-other-user", "case-other-tenant"),
            ),
        ),
        tenant_id="tenant-replay", user_id="user-replay", top_k=3,
    )
    assert report["result"] == "PASS"
    assert report["expected_recall_at_k"] == 1.0
    assert report["forbidden_leak_count"] == 0
    assert all(item["hits"][0]["source_ranks"]["lexical"] == 1 for item in report["cases"])
    frozen = json.loads((
        ROOT / "governance/evidence/m4-t04b/"
        "service-episode-offline-replay-v1.report.json"
    ).read_text("utf-8"))
    assert report == frozen
