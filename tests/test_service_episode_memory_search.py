"""ServiceEpisode search composition over the single active generation."""
from datetime import datetime, timezone

from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    EmbeddingProviderKind,
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
    ServiceEpisodeFreshnessMode,
    ServiceEpisodeRetrievalPolicy,
    ServiceEpisodeRetrievalPurpose,
    ServiceEpisodeRetriever,
)
from infrastructure.service_episode_embedding import ServiceEpisodeQueryEmbedder


SHA = "a" * 64


class QueryProvider:
    profile = EmbeddingProfile(
        provider="fixture-service-episode-query-provider-v1",
        provider_kind=EmbeddingProviderKind.MODEL,
        model="fixture-memory-model",
        model_version="revision-1",
        dimension=2,
        model_digest=SHA,
        document_preprocessing="raw-service-episode-canonical-v1",
        query_preprocessing="raw-memory-query-v1",
    )

    def embed_documents(self, texts):
        return [[0.2, 0.1] for _ in texts]

    def embed_queries(self, texts):
        return [[0.1, 0.2] for _ in texts]


def generation(*, state=GenerationState.ACTIVE, backend_id="POSTGRES_PG_FTS_ZH_V1"):
    return RetrievalGeneration(
        "generation-1", RetrievalCorpus.SERVICE_EPISODE, backend_id,
        "backend-fingerprint-v1", "service-episode-v1", "episode-outbox:42",
        "embedding-v1", 2, SHA, DistanceMetric.COSINE, "0.8.6", "HNSW",
        '{"ef_construction":64,"m":16}', "ascii-cjk-unigram-bigram-v1",
        "PG_FTS_ZH_V1", SHA, state,
    )


def profiled_generation(*, state=GenerationState.ACTIVE):
    value = QueryProvider.profile
    return RetrievalGeneration(
        generation_id="generation-1",
        corpus=RetrievalCorpus.SERVICE_EPISODE,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint="backend-fingerprint-v1",
        schema_version="service-episode-v1",
        source_watermark="episode-outbox:42",
        embedding_model=value.model,
        embedding_dimension=value.dimension,
        embedding_model_digest=value.model_digest,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=SHA,
        state=state,
        embedding_provider=value.provider,
        embedding_provider_kind=value.provider_kind,
        embedding_model_version=value.model_version,
        embedding_document_preprocessing=value.document_preprocessing,
        embedding_query_preprocessing=value.query_preprocessing,
    )


def policy(
    purpose=ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE,
):
    return ServiceEpisodeRetrievalPolicy(
        f"service-episode-{purpose.value.lower()}-fixture-v1",
        DEFAULT_MEMORY_RETRIEVAL_POLICY,
        0.0,
        90 * 86400,
        purpose,
        (
            ServiceEpisodeFreshnessMode.ANCHORED_OR_RECENT
            if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION
            else ServiceEpisodeFreshnessMode.HARD_WINDOW
        ),
        0.001 if purpose is ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION else None,
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


def purpose_service(generations, backends):
    retrievers = {
        purpose: ServiceEpisodeRetriever(backends[purpose], policy(purpose))
        for purpose in ServiceEpisodeRetrievalPurpose
    }
    return ServiceEpisodeMemorySearch(
        generations=generations,
        retrievers=retrievers,
        embed_query=lambda *_args: (0.1, 0.2),
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


def test_blank_entity_scope_fails_before_generation_or_provider_access():
    search = ServiceEpisodeMemorySearch.__new__(ServiceEpisodeMemorySearch)
    result = search.search(
        tenant_id="tenant-1",
        user_id="user-1",
        query="E401",
        entity_ids=("device-1", "  "),
    )

    assert (result.status, result.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "SEARCH_SCOPE_INCOMPLETE",
    )


def test_untyped_entity_or_explicit_time_flag_fails_before_provider_access():
    search = ServiceEpisodeMemorySearch.__new__(ServiceEpisodeMemorySearch)
    for kwargs in (
        {"entity_ids": (123,)},
        {"explicit_time_reference": "true"},
        {"top_k": "5"},
        {"query": None},
    ):
        result = search.search(
            tenant_id="tenant-1", user_id="user-1",
            **{"query": "E401", **kwargs},
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
    assert payload["purpose"] == "HISTORICAL_EVIDENCE"
    assert payload["purpose_outcome"] == "EVIDENCE_AVAILABLE"


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


def test_embedding_provider_failure_and_missing_vector_have_distinct_outcomes():
    backend = Backend()

    unavailable = service(
        Generations(generation()), backend,
        embed_query=lambda *_args: (_ for _ in ()).throw(
            TimeoutError("provider timeout")
        ),
    ).search(tenant_id="tenant-1", user_id="user-1", query="退款")
    missing = service(
        Generations(generation()), backend,
        embed_query=lambda *_args: None,
    ).search(tenant_id="tenant-1", user_id="user-1", query="退款")

    assert (unavailable.status, unavailable.detail_code) == (
        RetrievalStatus.UNAVAILABLE, "QUERY_EMBEDDING_PROVIDER_UNAVAILABLE",
    )
    assert (missing.status, missing.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "QUERY_EMBEDDING_MISSING",
    )
    assert backend.requests == []


def test_multiple_purpose_policies_require_an_explicit_purpose():
    backends = {
        purpose: Backend() for purpose in ServiceEpisodeRetrievalPurpose
    }
    search = purpose_service(Generations(generation()), backends)

    missing = search.search(
        tenant_id="tenant-1", user_id="user-1", query="上次的问题",
    )
    unsupported = search.search(
        tenant_id="tenant-1", user_id="user-1", query="上次的问题",
        purpose="ANSWER_FROM_MEMORY",
    )

    assert (missing.status, missing.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "RETRIEVAL_PURPOSE_REQUIRED",
    )
    assert (unsupported.status, unsupported.detail_code) == (
        RetrievalStatus.INVALID_CONTRACT, "RETRIEVAL_PURPOSE_UNSUPPORTED",
    )
    assert all(backend.requests == [] for backend in backends.values())


def test_selected_purpose_preserves_authenticated_scope_before_retrieval():
    backends = {
        purpose: Backend() for purpose in ServiceEpisodeRetrievalPurpose
    }
    search = purpose_service(Generations(generation()), backends)
    selected = ServiceEpisodeRetrievalPurpose.REFERENCE_RESOLUTION

    result = search.search(
        tenant_id="tenant-a",
        user_id="user-a",
        query="  订单 DP1234 上次那个问题  ",
        entity_ids=("DP1234",),
        purpose=selected,
        explicit_time_reference=True,
        top_k=3,
    )

    assert result.status is RetrievalStatus.OK
    assert result.purpose is selected
    assert result.policy_version == policy(selected).version
    assert backends[ServiceEpisodeRetrievalPurpose.HISTORICAL_EVIDENCE].requests == []
    request = backends[selected].requests[0]
    assert request.tenant_id == "tenant-a"
    assert request.scope.user_id == "user-a"
    assert request.scope.entity_ids == ("DP1234",)
    assert request.query_text == "订单 DP1234 上次那个问题"
    assert request.policy_fingerprint == policy(selected).fingerprint


def test_query_adapter_publishes_pinned_generation_and_profile_identity():
    backend = Backend()
    embedder = ServiceEpisodeQueryEmbedder(QueryProvider())
    search = ServiceEpisodeMemorySearch(
        generations=Generations(profiled_generation()),
        retriever=ServiceEpisodeRetriever(backend, policy()),
        embed_query=embedder,
    )

    result = search.search(
        tenant_id="tenant-1", user_id="user-1", query="上次的 E401",
    )

    assert result.status is RetrievalStatus.OK
    assert result.generation_id == "generation-1"
    assert result.embedding_profile_fingerprint == embedder.profile.fingerprint
    assert backend.requests[0].query_embedding == (0.1, 0.2)
