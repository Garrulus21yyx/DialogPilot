"""ServiceEpisode search composition over the single active generation."""
from datetime import datetime, timezone

from application.hybrid_retrieval import (
    DistanceMetric,
    GenerationState,
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)
from application.memory_retrieval_policy import DEFAULT_MEMORY_RETRIEVAL_POLICY
from application.service_episode_memory_search import ServiceEpisodeMemorySearch
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetriever,
)


SHA = "a" * 64


def generation(*, state=GenerationState.ACTIVE, backend_id="POSTGRES_PG_FTS_ZH_V1"):
    return RetrievalGeneration(
        "generation-1", RetrievalCorpus.SERVICE_EPISODE, backend_id,
        "backend-fingerprint-v1", "service-episode-v1", "episode-outbox:42",
        "embedding-v1", 2, SHA, DistanceMetric.COSINE, "0.8.6", "HNSW",
        '{"ef_construction":64,"m":16}', "ascii-cjk-unigram-bigram-v1",
        "PG_FTS_ZH_V1", SHA, state,
    )


def policy():
    return ServiceEpisodeRetrievalPolicy(
        "service-episode-policy-fixture-v1", DEFAULT_MEMORY_RETRIEVAL_POLICY,
        0.0, 90 * 86400,
    )


class Generations:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error

    def active(self, corpus, *, backend_id):
        assert corpus is RetrievalCorpus.SERVICE_EPISODE
        assert backend_id == "POSTGRES_PG_FTS_ZH_V1"
        if self.error is not None:
            raise self.error
        return self.value


class Backend:
    def __init__(self):
        self.requests = []

    def retrieve(self, request):
        self.requests.append(request)
        item = RetrievalCandidate(
            "candidate-1", RetrievalCorpus.SERVICE_EPISODE, "generation-1",
            "case-1", "1", 1, 0.9, SHA,
            datetime.now(timezone.utc).isoformat(),
        )
        return HybridRetrievalResult(
            RetrievalStatus.OK, request.backend_fingerprint, request.generation_id,
            lexical_candidates=(item,), index_watermark="episode-outbox:42",
        )


def service(generations, backend, *, embed_query=lambda *_args: (0.1, 0.2)):
    retrieval_policy = policy()
    return ServiceEpisodeMemorySearch(
        generations=generations,
        retriever=ServiceEpisodeRetriever(backend, retrieval_policy),
        embed_query=embed_query,
    )


def test_missing_active_generation_fails_before_backend_or_embedding():
    backend = Backend()
    result = service(
        Generations(error=LookupError("missing")), backend,
        embed_query=lambda *_args: (_ for _ in ()).throw(
            AssertionError("generation resolution must stop first")
        ),
    ).search(tenant_id="tenant-1", user_id="user-1", query="E401")
    assert (result.status, result.detail_code) == (
        RetrievalStatus.UNAVAILABLE, "GENERATION_UNAVAILABLE",
    )
    assert backend.requests == []


def test_visually_blank_query_fails_before_generation_or_provider_access():
    search = ServiceEpisodeMemorySearch.__new__(ServiceEpisodeMemorySearch)
    for query in ("\u200b\ufeff", "\u00a0\u2003\u2028\u3000"):
        result = search.search(
            tenant_id="tenant-1", user_id="user-1", query=query,
        )
        assert (result.status, result.detail_code) == (
            RetrievalStatus.INVALID_CONTRACT, "SEARCH_SCOPE_INCOMPLETE",
        )


def test_active_generation_builds_authenticated_scoped_request_and_trace():
    backend = Backend()
    result = service(Generations(generation()), backend).search(
        tenant_id="tenant-1", user_id="user-1", query="E401 登录失败",
        entity_ids=("device-1",), top_k=3,
    )
    assert result.status is RetrievalStatus.OK
    request = backend.requests[0]
    assert request.tenant_id == "tenant-1"
    assert request.scope.user_id == "user-1"
    assert request.scope.entity_ids == ("device-1",)
    assert request.backend_fingerprint == "backend-fingerprint-v1"
    assert request.generation_id == "generation-1"
    assert request.policy_fingerprint == policy().fingerprint
    payload = result.to_dict()
    assert payload["hits"][0]["episode_id"] == "case-1"
    assert payload["hits"][0]["index_watermark"] == "episode-outbox:42"


def test_non_active_generation_fails_before_backend():
    backend = Backend()
    result = service(
        Generations(generation(state=GenerationState.READY)), backend,
    ).search(tenant_id="tenant-1", user_id="user-1", query="退款")
    assert (result.status, result.detail_code) == (
        RetrievalStatus.CONFLICT, "ACTIVE_GENERATION_DRIFT",
    )
    assert backend.requests == []


def test_embedding_contract_failure_does_not_silently_become_lexical_only():
    backend = Backend()
    result = service(
        Generations(generation()), backend,
        embed_query=lambda *_args: (_ for _ in ()).throw(ValueError("drift")),
    ).search(tenant_id="tenant-1", user_id="user-1", query="退款")
    assert (result.status, result.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "QUERY_EMBEDDING_CONTRACT",
    )
    assert backend.requests == []
