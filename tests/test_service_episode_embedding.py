"""ServiceEpisode query embedding is pinned to its generation contract."""
from dataclasses import replace

import pytest

from application.hybrid_retrieval import (
    DistanceMetric,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.service_episode_embedding import (
    ServiceEpisodeEmbeddingContractError,
    ServiceEpisodeQueryEmbedder,
)


SHA = "a" * 64


def generation():
    return RetrievalGeneration(
        "generation-1", RetrievalCorpus.SERVICE_EPISODE, "POSTGRES_HYBRID_V1",
        "backend-v1", "service-episode-v1", "episode-outbox:1",
        "dialogpilot-hash-embedding-v1", 384, SHA, DistanceMetric.COSINE, "0.8.6",
        "HNSW", '{"ef_construction":64,"m":16}',
        "ascii-cjk-unigram-bigram-v1", "PG_FTS_ZH_V1", SHA,
    )


def test_embedder_accepts_one_finite_vector_matching_generation():
    embedder = ServiceEpisodeQueryEmbedder(
        lambda queries: [[float(index) / 384 for index in range(384)]],
    )
    vector = embedder("E401 登录失败", generation())
    assert len(vector) == 384
    assert vector[1] == pytest.approx(1 / 384)


def test_default_local_embedding_is_deterministic_normalized_and_dependency_free():
    embedder = ServiceEpisodeQueryEmbedder()
    first = embedder("订单 A123 重复扣款", generation())
    second = embedder("订单 A123 重复扣款", generation())
    assert first == second
    assert len(first) == 384
    assert sum(value * value for value in first) == pytest.approx(1.0)


@pytest.mark.parametrize("changed", [
    {"embedding_model": "different"},
    {"embedding_dimension": 768},
    {"embedding_model_digest": "UNVERIFIED:model"},
])
def test_embedder_rejects_generation_drift_before_provider(changed):
    called = False

    def provider(_queries):
        nonlocal called
        called = True
        return [[0.0] * 384]

    with pytest.raises(ServiceEpisodeEmbeddingContractError, match="unsupported"):
        ServiceEpisodeQueryEmbedder(provider)(
            "E401", replace(generation(), **changed),
        )
    assert called is False


@pytest.mark.parametrize("vectors", [
    [],
    [[0.0] * 383],
    [[float("nan")] * 384],
    [[0.0] * 384, [0.0] * 384],
])
def test_embedder_rejects_provider_shape_or_numeric_drift(vectors):
    with pytest.raises(ServiceEpisodeEmbeddingContractError, match="provider"):
        ServiceEpisodeQueryEmbedder(lambda _queries: vectors)(
            "E401", generation(),
        )
