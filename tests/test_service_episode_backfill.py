"""M4-T04B legacy inventory, frozen policy and shadow attribution proofs."""
from dataclasses import replace

from application.hybrid_retrieval import (
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)
from application.memory_retrieval_policy import (
    LEGACY_MEMORY_RETRIEVAL_POLICY,
    MemoryRetrievalBinding,
    MemoryRetrievalConsumerMode,
)
from application.service_episode_backfill import (
    BackfillDisposition,
    ServiceEpisodeBackfillPolicy,
    legacy_record,
)
from evaluation.service_episode_shadow import (
    EpisodeQueryCapture,
    capture_query,
    compare_capture,
)
from memory.hybrid_retrieval import HybridMemoryRetriever


SHA = "a" * 64


def _candidate(source, rank, *, provenance=SHA):
    return RetrievalCandidate(
        f"candidate-{source}", RetrievalCorpus.SERVICE_EPISODE,
        "generation", source, "1", rank, 1.0 / rank, provenance,
    )


def _result(*, dense=(), lexical=(), status=RetrievalStatus.OK, generation="generation"):
    return HybridRetrievalResult(
        status, "backend", generation,
        tuple(replace(item, generation_id=generation) for item in dense),
        tuple(replace(item, generation_id=generation) for item in lexical),
    )


def test_legacy_memory_policy_is_frozen_and_owned_by_retriever():
    policy = LEGACY_MEMORY_RETRIEVAL_POLICY
    assert (
        policy.vector_weight, policy.lexical_weight, policy.recency_weight,
        policy.rrf_k, policy.lexical_pool,
    ) == (0.30, 0.60, 0.10, 60, 20)
    retriever = HybridMemoryRetriever()
    assert retriever.policy_fingerprint == policy.fingerprint


def test_current_legacy_chunk_is_retained_not_inferred_as_resolution():
    record = legacy_record(
        "legacy-1", "assistant: issue fixed", {
            "user_id": "user-1", "conv_id": "conversation-1",
            "message_id": "message-1", "role": "assistant",
        },
    )
    decision = ServiceEpisodeBackfillPolicy().decide(record)
    assert decision.disposition is BackfillDisposition.RETAIN_CONVERSATION_ONLY
    assert set(decision.reason_codes) >= {
        "MISSING_TENANT", "MISSING_SOURCE_EVENTS", "MISSING_CASE",
        "CASE_NOT_CLOSED", "MISSING_OUTCOME_VERIFICATION",
        "OUTCOME_NOT_ACCEPTED",
    }
    assert decision.episode_id is None


def test_inventory_is_order_independent_replayable_and_tombstone_fenced():
    metadata = {
        "tenant_id": "tenant-1", "user_id": "user-1",
        "conversation_id": "conversation-1",
        "source_event_refs": '["event:1"]', "case_id": "case-1",
        "case_status": "closed", "outcome_verification_ref": "receipt:1",
        "outcome_verification_status": "ACCEPTED", "role": "user",
    }
    eligible = legacy_record("b", "verified issue", metadata)
    rejected = replace(legacy_record("a", "deleted issue", metadata), tombstoned=True)
    policy = ServiceEpisodeBackfillPolicy()
    first = policy.inventory((eligible, rejected), source_watermark="chroma:2")
    replay = policy.inventory((rejected, eligible), source_watermark="chroma:2")
    assert first == replay
    assert (first.record_count, first.eligible_count, first.retained_count) == (2, 1, 1)
    assert first.decisions[0].reason_codes == ("SUBJECT_TOMBSTONED",)
    assert first.decisions[1].episode_id == "case-1"


def test_memory_binding_rolls_back_policy_backend_and_corpus_as_one_tuple():
    binding = MemoryRetrievalBinding(
        MemoryRetrievalConsumerMode.SHADOW, "b" * 64, "pg", "pg-gen",
        "episode-gen", "a" * 64, "legacy", "legacy-gen",
        "raw-memory-v4", 3,
    )
    rolled = binding.rollback()
    assert rolled.mode is MemoryRetrievalConsumerMode.LEGACY
    assert (
        rolled.policy_fingerprint, rolled.backend_id,
        rolled.backend_generation, rolled.corpus_generation,
    ) == ("a" * 64, "legacy", "legacy-gen", "raw-memory-v4")
    assert rolled.version == 4


def test_same_query_shadow_separates_corpus_and_ranking_differences():
    capture_id, query_hash = capture_query("login issue")
    legacy = _result(
        dense=(_candidate("episode-a", 1),),
        lexical=(_candidate("episode-b", 1),),
        generation="legacy-generation",
    )
    ranking_target = _result(
        dense=(_candidate("episode-b", 1), _candidate("episode-a", 2)),
        lexical=(), generation="target-generation",
    )
    captured = EpisodeQueryCapture(
        capture_id, query_hash, legacy, ranking_target,
        LEGACY_MEMORY_RETRIEVAL_POLICY.fingerprint, "c" * 64,
        (("episode-a", "2026-09-01T00:00:00+00:00"),
         ("episode-b", "2026-09-02T00:00:00+00:00")),
        (("episode-a", "2026-09-01T00:00:00+00:00"),
         ("episode-b", "2026-09-02T00:00:00+00:00")),
    )
    ranking = compare_capture(captured)
    assert ranking["classification"] == "RANKING_DIFFERENCE"
    assert ranking["continuity_match"] is True
    assert ranking["freshness_comparable"] is True
    assert ranking["freshness_match"] is True
    corpus = compare_capture(replace(
        captured,
        target=_result(
            dense=(_candidate("episode-c", 1),),
            generation="target-generation",
        ),
    ))
    assert corpus["classification"] == "CORPUS_DIFFERENCE"
    assert corpus["continuity_match"] is False


def test_shadow_reports_unavailable_without_treating_it_as_no_evidence():
    capture_id, query_hash = capture_query("refund")
    unavailable = HybridRetrievalResult(
        RetrievalStatus.UNAVAILABLE, "backend", "target-generation",
        detail_code="BACKEND_DOWN",
    )
    report = compare_capture(EpisodeQueryCapture(
        capture_id, query_hash,
        _result(dense=(_candidate("episode-a", 1),)), unavailable,
        "a" * 64, "b" * 64,
    ))
    assert report["classification"] == "UNAVAILABLE"
    assert report["target_status"] == "UNAVAILABLE"
