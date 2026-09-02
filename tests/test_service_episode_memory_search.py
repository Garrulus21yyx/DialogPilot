"""Direct-cutover ServiceEpisode tool composition contracts."""
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
from application.memory_retrieval_policy import (
    LEGACY_MEMORY_RETRIEVAL_POLICY,
    MemoryRetrievalBinding,
    MemoryRetrievalTarget,
)
from application.service_episode_memory_search import ServiceEpisodeMemorySearch
from application.service_episode_retriever import (
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetriever,
)


SHA = "a" * 64


def generation():
    return RetrievalGeneration(
        "generation-1", RetrievalCorpus.SERVICE_EPISODE, "POSTGRES_HYBRID_V1",
        "backend-fingerprint-v1", "service-episode-v1", "episode-outbox:42",
        "embedding-v1", 2, SHA, DistanceMetric.COSINE, "0.8.6", "HNSW",
        '{"ef_construction":64,"m":16}', "ascii-cjk-unigram-bigram-v1",
        "PG_FTS_ZH_V1", SHA, GenerationState.READY,
    )


def policy():
    return ServiceEpisodeRetrievalPolicy(
        "service-episode-policy-fixture-v1", LEGACY_MEMORY_RETRIEVAL_POLICY,
        0.0, 90 * 86400,
    )


class Bindings:
    def __init__(self, value):
        self.value = value

    def get(self, tenant_id):
        assert tenant_id == "tenant-1"
        return self.value


class Generations:
    def __init__(self, value):
        self.value = value

    def get(self, generation_id):
        assert generation_id == "generation-1"
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


def binding(retrieval_policy, *, enabled):
    return MemoryRetrievalBinding(MemoryRetrievalTarget(
        retrieval_policy.fingerprint, "POSTGRES_HYBRID_V1", "generation-1",
        "episode-outbox:42",
    ), enabled, 1)


def test_disabled_binding_prevents_any_backend_or_embedding_call():
    retrieval_policy = policy()
    backend = Backend()

    def forbidden(*_args):
        raise AssertionError("disabled binding must stop before providers")

    service = ServiceEpisodeMemorySearch(
        bindings=Bindings(binding(retrieval_policy, enabled=False)),
        generations=Generations(generation()),
        retriever=ServiceEpisodeRetriever(backend, retrieval_policy),
        embed_query=forbidden,
    )
    result = service.search(
        tenant_id="tenant-1", user_id="user-1", query="E401",
    )
    assert (result.status, result.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "DIRECT_CUTOVER_DISABLED",
    )
    assert backend.requests == []


def test_enabled_binding_builds_authenticated_scoped_request_and_trace():
    retrieval_policy = policy()
    backend = Backend()
    service = ServiceEpisodeMemorySearch(
        bindings=Bindings(binding(retrieval_policy, enabled=True)),
        generations=Generations(generation()),
        retriever=ServiceEpisodeRetriever(backend, retrieval_policy),
        embed_query=lambda _query, _generation: (0.1, 0.2),
    )
    result = service.search(
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
    payload = result.to_dict()
    assert payload["hits"][0]["episode_id"] == "case-1"
    assert payload["hits"][0]["index_watermark"] == "episode-outbox:42"


def test_generation_or_policy_binding_drift_fails_before_backend():
    retrieval_policy = policy()
    backend = Backend()
    drifted_generation = RetrievalGeneration(**{
        **generation().__dict__, "source_watermark": "different",
    })
    service = ServiceEpisodeMemorySearch(
        bindings=Bindings(binding(retrieval_policy, enabled=True)),
        generations=Generations(drifted_generation),
        retriever=ServiceEpisodeRetriever(backend, retrieval_policy),
        embed_query=lambda *_args: None,
    )
    result = service.search(
        tenant_id="tenant-1", user_id="user-1", query="退款",
    )
    assert (result.status, result.detail_code) == (
        RetrievalStatus.CONFLICT, "GENERATION_BINDING_DRIFT",
    )
    assert backend.requests == []


def test_embedding_contract_failure_does_not_silently_become_lexical_only():
    retrieval_policy = policy()
    backend = Backend()
    service = ServiceEpisodeMemorySearch(
        bindings=Bindings(binding(retrieval_policy, enabled=True)),
        generations=Generations(generation()),
        retriever=ServiceEpisodeRetriever(backend, retrieval_policy),
        embed_query=lambda *_args: (_ for _ in ()).throw(ValueError("drift")),
    )
    result = service.search(
        tenant_id="tenant-1", user_id="user-1", query="退款",
    )
    assert (result.status, result.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "QUERY_EMBEDDING_CONTRACT",
    )
    assert backend.requests == []
