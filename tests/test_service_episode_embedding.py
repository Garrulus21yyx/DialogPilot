"""ServiceEpisode document/query embedding share one truthful generation profile."""
from dataclasses import replace

import pytest

from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    EmbeddingProviderKind,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.service_episode_embedding import (
    LocalHashServiceEpisodeEmbeddingBaseline,
    ServiceEpisodeDocumentEmbedder,
    ServiceEpisodeEmbeddingContractError,
    ServiceEpisodeEmbeddingFailure,
    ServiceEpisodeEmbeddingUnavailable,
    ServiceEpisodeQueryEmbedder,
)


SHA = "a" * 64


class Provider:
    def __init__(self, profile, *, documents=None, queries=None):
        self.profile = profile
        self.documents = documents or (lambda texts: [[0.1, 0.2, 0.3] for _ in texts])
        self.queries = queries or (lambda texts: [[0.3, 0.2, 0.1] for _ in texts])
        self.document_inputs = []
        self.query_inputs = []

    def embed_documents(self, texts):
        self.document_inputs.append(tuple(texts))
        return self.documents(texts)

    def embed_queries(self, texts):
        self.query_inputs.append(tuple(texts))
        return self.queries(texts)


def profile(**changed):
    values = {
        "provider": "sentence-transformers-explicit-adapter-v1",
        "provider_kind": EmbeddingProviderKind.MODEL,
        "model": "BAAI/bge-m3",
        "model_version": "pinned-revision-1",
        "dimension": 3,
        "model_digest": SHA,
        "document_preprocessing": "bge-m3-document-raw-text-v1",
        "query_preprocessing": "bge-m3-query-raw-text-v1",
    }
    values.update(changed)
    return EmbeddingProfile(**values)


def generation(value=None, **changed):
    value = value or profile()
    fields = {
        "generation_id": "generation-1",
        "corpus": RetrievalCorpus.SERVICE_EPISODE,
        "backend_id": "POSTGRES_PG_FTS_ZH_V1",
        "backend_fingerprint": "backend-v1",
        "schema_version": "service-episode-v1",
        "source_watermark": "episode-outbox:1",
        "embedding_model": value.model,
        "embedding_dimension": value.dimension,
        "embedding_model_digest": value.model_digest,
        "distance_metric": DistanceMetric.COSINE,
        "vector_extension_version": "0.8.6",
        "index_method": "HNSW",
        "index_params_json": '{"ef_construction":64,"m":16}',
        "chinese_tokenizer": "ascii-cjk-unigram-bigram-v1",
        "lexical_ranker": "PG_FTS_ZH_V1",
        "manifest_hash": SHA,
        "embedding_provider": value.provider,
        "embedding_provider_kind": value.provider_kind,
        "embedding_model_version": value.model_version,
        "embedding_document_preprocessing": value.document_preprocessing,
        "embedding_query_preprocessing": value.query_preprocessing,
    }
    fields.update(changed)
    return RetrievalGeneration(**fields)


def test_document_and_query_paths_preserve_raw_inputs_and_share_profile():
    provider = Provider(profile())
    document = ServiceEpisodeDocumentEmbedder(provider)
    query = ServiceEpisodeQueryEmbedder(provider)

    document_vectors = document(
        ("退款 原始 Canonical Text",), generation().embedding_profile,
    )
    query_vector = query("上次退款怎么解决", generation())

    assert document_vectors == ((0.1, 0.2, 0.3),)
    assert query_vector == (0.3, 0.2, 0.1)
    assert provider.document_inputs == [("退款 原始 Canonical Text",)]
    assert provider.query_inputs == [("上次退款怎么解决",)]
    assert query.profile.fingerprint == generation().embedding_profile.fingerprint


@pytest.mark.parametrize(
    "changed",
    [
        {"embedding_provider": "different-provider"},
        {"embedding_model": "different-model"},
        {"embedding_model_version": "different-version"},
        {"embedding_dimension": 4},
        {"embedding_model_digest": "b" * 64},
        {"embedding_document_preprocessing": "different-document-preprocessing"},
        {"embedding_query_preprocessing": "different-query-preprocessing"},
    ],
)
def test_query_rejects_every_generation_profile_drift_before_provider(changed):
    provider = Provider(profile())
    with pytest.raises(ServiceEpisodeEmbeddingContractError) as raised:
        ServiceEpisodeQueryEmbedder(provider)(
            "E401", replace(generation(), **changed),
        )

    assert raised.value.code is ServiceEpisodeEmbeddingFailure.PROFILE_MISMATCH
    assert provider.query_inputs == []


def test_incomplete_legacy_generation_is_not_a_dense_serving_contract():
    provider = Provider(profile())
    legacy = generation(
        embedding_provider="legacy-unrecorded",
        embedding_provider_kind=EmbeddingProviderKind.LEGACY_UNSPECIFIED,
        embedding_model_version="legacy-unrecorded",
        embedding_document_preprocessing="legacy-unrecorded",
        embedding_query_preprocessing="legacy-unrecorded",
    )

    with pytest.raises(ServiceEpisodeEmbeddingContractError) as raised:
        ServiceEpisodeQueryEmbedder(provider)("E401", legacy)

    assert raised.value.code is ServiceEpisodeEmbeddingFailure.PROFILE_INCOMPLETE


def test_provider_without_complete_profile_is_rejected_at_composition():
    class MissingProfile:
        pass

    with pytest.raises(ServiceEpisodeEmbeddingContractError) as raised:
        ServiceEpisodeQueryEmbedder(MissingProfile())

    assert raised.value.code is ServiceEpisodeEmbeddingFailure.PROFILE_INCOMPLETE


def test_hash_provider_is_explicitly_baseline_only():
    provider = LocalHashServiceEpisodeEmbeddingBaseline()
    baseline_generation = generation(provider.profile)

    with pytest.raises(ServiceEpisodeEmbeddingContractError) as raised:
        ServiceEpisodeQueryEmbedder(provider)("订单 A123", baseline_generation)
    assert raised.value.code is ServiceEpisodeEmbeddingFailure.BASELINE_FORBIDDEN

    query = ServiceEpisodeQueryEmbedder(provider, allow_hash_baseline=True)
    first = query("订单 A123", baseline_generation)
    second = query("订单 A123", baseline_generation)
    assert first == second
    assert len(first) == provider.profile.dimension
    assert sum(value * value for value in first) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "vectors",
    [
        [],
        [[0.0, 0.0]],
        [[float("nan"), 0.0, 0.0]],
        [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
    ],
)
def test_provider_shape_dimension_and_numeric_drift_are_typed(vectors):
    provider = Provider(profile(), queries=lambda _texts: vectors)
    with pytest.raises(ServiceEpisodeEmbeddingContractError) as raised:
        ServiceEpisodeQueryEmbedder(provider)("E401", generation())

    assert (
        raised.value.code
        is ServiceEpisodeEmbeddingFailure.PROVIDER_OUTPUT_INVALID
    )


def test_provider_runtime_failure_is_distinct_from_contract_failure():
    def unavailable(_texts):
        raise TimeoutError("model endpoint timed out")

    provider = Provider(profile(), queries=unavailable)
    with pytest.raises(ServiceEpisodeEmbeddingUnavailable) as raised:
        ServiceEpisodeQueryEmbedder(provider)("E401", generation())

    assert raised.value.code is ServiceEpisodeEmbeddingFailure.PROVIDER_UNAVAILABLE
