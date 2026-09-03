"""M4-T04 ServiceEpisode relevance/freshness fusion invariants."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from application.hybrid_retrieval import (
    EpisodeSearchScope,
    HybridRetrievalRequest,
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalStatus,
)
from application.memory_retrieval_policy import DEFAULT_MEMORY_RETRIEVAL_POLICY
from application.service_episode_retriever import (
    ServiceEpisodeFreshnessMode,
    ServiceEpisodePurposeOutcome,
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetrievalPurpose,
    ServiceEpisodeRetrievalResult,
    ServiceEpisodeRetriever,
)


NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)
SHA = "a" * 64


class Backend:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def retrieve(self, _request):
        self.calls += 1
        return self.result


def policy(
    *,
    minimum=0.0,
    max_age=90 * 86400,
    purpose=ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE,
    freshness_mode=ServiceEpisodeFreshnessMode.HARD_WINDOW,
    unique_binding_margin=None,
):
    return ServiceEpisodeRetrievalPolicy(
        "service-episode-retrieval-policy-heldout-v1",
        DEFAULT_MEMORY_RETRIEVAL_POLICY,
        minimum,
        max_age,
        purpose,
        freshness_mode,
        unique_binding_margin,
    )


def candidate(name, rank, freshness, *, score=1.0):
    return RetrievalCandidate(
        f"candidate-{name}", RetrievalCorpus.SERVICE_EPISODE,
        "generation-1", f"case-{name}", "1", rank, score, SHA,
        freshness,
    )


def request(retrieval_policy):
    return HybridRetrievalRequest(
        "tenant-1", RetrievalCorpus.SERVICE_EPISODE, "backend-1",
        "generation-1", retrieval_policy.fingerprint, "E401 登录失败",
        None, EpisodeSearchScope("user-1"),
    )


def result(*, dense=(), lexical=(), watermark="episode-outbox:42"):
    return HybridRetrievalResult(
        RetrievalStatus.OK, "backend-1", "generation-1",
        dense_candidates=dense, lexical_candidates=lexical,
        index_watermark=watermark,
    )


def test_similar_fault_fuses_routes_and_records_policy_source_ranks():
    retrieval_policy = policy()
    general = candidate("general", 1, "2026-09-02T00:00:00+00:00")
    exact = candidate("e401", 2, "2026-08-20T00:00:00+00:00")
    ranked = ServiceEpisodeRetriever(Backend(result(
        dense=(general, exact), lexical=(replace(exact, rank=1),),
    )), retrieval_policy).retrieve(request(retrieval_policy), now=NOW)

    assert ranked.status is RetrievalStatus.OK
    assert [item.episode_id for item in ranked.hits] == ["case-e401", "case-general"]
    assert ranked.hits[0].source_ranks == (
        ("vector", 2), ("lexical", 1), ("recency", 2),
    )
    assert ranked.hits[0].policy_fingerprint == retrieval_policy.fingerprint
    assert ranked.hits[0].index_watermark == "episode-outbox:42"


def test_freshness_is_a_separate_gate_and_recency_never_adds_candidates():
    retrieval_policy = policy(max_age=86400)
    stale = candidate("stale", 1, "2026-08-01T00:00:00+00:00")
    ranked = ServiceEpisodeRetriever(
        Backend(result(dense=(stale,))), retrieval_policy,
    ).retrieve(request(retrieval_policy), now=NOW)

    assert ranked.status is RetrievalStatus.NO_EVIDENCE
    assert ranked.detail_code == "FRESHNESS_THRESHOLD"
    assert ranked.hits == ()


def test_relevance_gate_is_independent_and_policy_must_be_pinned():
    retrieval_policy = policy(minimum=1.0)
    weak = candidate("weak", 1, "2026-09-01T00:00:00+00:00")
    ranked = ServiceEpisodeRetriever(
        Backend(result(lexical=(weak,))), retrieval_policy,
    ).retrieve(request(retrieval_policy), now=NOW)
    assert (ranked.status, ranked.detail_code) == (
        RetrievalStatus.NO_EVIDENCE, "RELEVANCE_THRESHOLD",
    )

    drifted = replace(request(retrieval_policy), policy_fingerprint="drift")
    rejected = ServiceEpisodeRetriever(
        Backend(result(lexical=(weak,))), retrieval_policy,
    ).retrieve(drifted, now=NOW)
    assert rejected.status is RetrievalStatus.INVALID_CONTRACT


def test_candidate_authority_conflict_and_missing_watermark_fail_closed():
    retrieval_policy = policy()
    item = candidate("same", 1, "2026-09-01T00:00:00+00:00")
    conflict = ServiceEpisodeRetriever(Backend(result(
        dense=(item,), lexical=(replace(item, provenance_sha256="b" * 64),),
    )), retrieval_policy).retrieve(request(retrieval_policy), now=NOW)
    assert (conflict.status, conflict.detail_code) == (
        RetrievalStatus.CONFLICT, "CANDIDATE_AUTHORITY_CONFLICT",
    )

    missing = ServiceEpisodeRetriever(
        Backend(result(dense=(item,), watermark="")), retrieval_policy,
    ).retrieve(request(retrieval_policy), now=NOW)
    assert (missing.status, missing.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "INDEX_WATERMARK_MISSING",
    )


def test_reference_and_historical_policies_are_not_interchangeable():
    reference = policy(
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        freshness_mode=ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT,
        unique_binding_margin=0.001,
    )
    backend = Backend(result(lexical=(
        candidate("one", 1, "2026-09-01T00:00:00+00:00"),
    )))
    rejected = ServiceEpisodeRetriever(backend, reference).retrieve(
        request(reference),
        purpose=ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE,
        now=NOW,
    )

    assert (rejected.status, rejected.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "RETRIEVAL_PURPOSE_MISMATCH",
    )
    assert rejected.purpose is ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE
    assert rejected.purpose_outcome is ServiceEpisodePurposeOutcome.FAILED


def test_reference_resolution_requires_a_unique_score_margin():
    reference = policy(
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        freshness_mode=ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT,
        unique_binding_margin=0.001,
    )
    first = candidate("one", 1, "2026-09-01T00:00:00+00:00")
    second = candidate("two", 2, "2026-09-01T00:00:00+00:00")
    ambiguous = ServiceEpisodeRetriever(Backend(result(
        lexical=(first, second),
    )), reference).retrieve(
        request(reference),
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        now=NOW,
    )

    assert (ambiguous.status, ambiguous.detail_code) == (
        RetrievalStatus.AMBIGUOUS, "REFERENCE_MARGIN_NOT_MET",
    )
    assert [hit.episode_id for hit in ambiguous.hits] == [
        "case-one", "case-two",
    ]
    assert ambiguous.purpose_outcome is ServiceEpisodePurposeOutcome.AMBIGUOUS


def test_historical_evidence_keeps_old_relevant_history_when_age_is_rank_only():
    historical = policy(
        max_age=None,
        freshness_mode=ServiceEpisodeFreshnessMode.RANK_ONLY,
    )
    old = candidate("old", 1, "2020-01-01T00:00:00+00:00")
    ranked = ServiceEpisodeRetriever(
        Backend(result(dense=(old,))), historical,
    ).retrieve(
        request(historical),
        purpose=ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE,
        now=NOW,
    )

    assert ranked.status is RetrievalStatus.OK
    assert (
        ranked.purpose_outcome
        is ServiceEpisodePurposeOutcome.EVIDENCE_AVAILABLE
    )
    assert [hit.episode_id for hit in ranked.hits] == ["case-old"]


def test_reference_policy_allows_old_history_only_with_an_explicit_anchor():
    reference = policy(
        max_age=86400,
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        freshness_mode=ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT,
        unique_binding_margin=0.001,
    )
    old = candidate("old", 1, "2020-01-01T00:00:00+00:00")
    retriever = ServiceEpisodeRetriever(Backend(result(lexical=(old,))), reference)

    unanchored = retriever.retrieve(
        request(reference),
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        now=NOW,
    )
    anchored = retriever.retrieve(
        request(reference),
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        explicit_time_reference=True,
        now=NOW,
    )

    assert (unanchored.status, unanchored.detail_code) == (
        RetrievalStatus.NO_EVIDENCE, "FRESHNESS_THRESHOLD",
    )
    assert anchored.status is RetrievalStatus.OK
    assert anchored.purpose_outcome is ServiceEpisodePurposeOutcome.UNIQUE_BINDING
    assert anchored.hits[0].episode_id == "case-old"


def test_policy_rejects_cross_purpose_and_freshness_illegal_states():
    with pytest.raises(ValueError, match="unique-binding"):
        policy(
            purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
            freshness_mode=ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT,
        )
    with pytest.raises(ValueError, match="must not apply"):
        policy(unique_binding_margin=0.01)
    with pytest.raises(ValueError, match="must not declare"):
        policy(freshness_mode=ServiceEpisodeFreshnessMode.RANK_ONLY)
    with pytest.raises(ValueError, match="purpose is invalid"):
        policy(purpose="REFERENCE_RESOLUTION")
    with pytest.raises(ValueError, match="freshness mode is invalid"):
        policy(freshness_mode="HARD_WINDOW")


def test_reference_resolution_requires_runner_up_and_typed_anchor_flag():
    reference = policy(
        purpose=ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION,
        freshness_mode=ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT,
        unique_binding_margin=0.001,
    )
    backend = Backend(result(lexical=(
        candidate("one", 1, "2026-09-01T00:00:00+00:00"),
    )))
    retriever = ServiceEpisodeRetriever(backend, reference)

    too_small = retriever.retrieve(request(reference), top_k=1, now=NOW)
    bad_anchor = retriever.retrieve(
        request(reference), explicit_time_reference="true", now=NOW,
    )

    assert (too_small.status, too_small.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "REFERENCE_TOP_K_TOO_SMALL",
    )
    assert (bad_anchor.status, bad_anchor.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "EXPLICIT_TIME_REFERENCE_INVALID",
    )
    assert backend.calls == 0


def test_result_algebra_rejects_unbound_success_and_ambiguous_evidence_use():
    with pytest.raises(ValueError, match="requires evidence"):
        ServiceEpisodeRetrievalResult(RetrievalStatus.OK)
    with pytest.raises(ValueError, match="ambiguous reference"):
        ServiceEpisodeRetrievalResult(
            RetrievalStatus.AMBIGUOUS,
            purpose=ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE,
        )
