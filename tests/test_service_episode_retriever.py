"""M4-T04 ServiceEpisode relevance/freshness fusion invariants."""
from dataclasses import replace
from datetime import datetime, timezone

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
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetriever,
)


NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)
SHA = "a" * 64


class Backend:
    def __init__(self, result):
        self.result = result

    def retrieve(self, _request):
        return self.result


def policy(*, minimum=0.0, max_age=90 * 86400):
    return ServiceEpisodeRetrievalPolicy(
        "service-episode-retrieval-policy-heldout-v1",
        DEFAULT_MEMORY_RETRIEVAL_POLICY,
        minimum,
        max_age,
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
