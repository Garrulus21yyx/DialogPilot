from __future__ import annotations

import pytest

from application.hybrid_retrieval import EmbeddingProviderKind
from infrastructure.bge_m3_embedding import BGEM3EmbeddingUnavailable
from infrastructure.dense_embedding_factory import (
    KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
    SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
    DenseEmbeddingProviderFactory,
    DenseEmbeddingProviderSelectionError,
)
from infrastructure.knowledge_embedding import (
    LocalHashKnowledgeEmbeddingBaseline,
)
from infrastructure.service_episode_embedding import (
    LocalHashServiceEpisodeEmbeddingBaseline,
)


SHA = "a" * 64


def test_default_builds_each_explicit_corpus_hash_baseline() -> None:
    factory = DenseEmbeddingProviderFactory({})

    knowledge = factory.build(
        selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
    )
    memory = factory.build(
        selection_key=SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashServiceEpisodeEmbeddingBaseline,
    )

    assert isinstance(knowledge, LocalHashKnowledgeEmbeddingBaseline)
    assert isinstance(memory, LocalHashServiceEpisodeEmbeddingBaseline)
    assert knowledge.profile.provider_kind is EmbeddingProviderKind.HASH_BASELINE
    assert memory.profile.provider_kind is EmbeddingProviderKind.HASH_BASELINE


def test_bge_m3_uses_from_env_and_shares_one_model_provider(
    tmp_path, monkeypatch,
) -> None:
    captured = []

    class FakeProvider:
        def __init__(self, config) -> None:
            captured.append(config)

    monkeypatch.setattr(
        "infrastructure.dense_embedding_factory.LocalBGEM3EmbeddingProvider",
        FakeProvider,
    )
    factory = DenseEmbeddingProviderFactory({
        KNOWLEDGE_DENSE_EMBEDDING_PROVIDER: "bge_m3",
        SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER: "bge_m3",
        "BGE_M3_LOCAL_MODEL_PATH": str(tmp_path),
        "BGE_M3_MODEL_REVISION": "commit-one",
        "BGE_M3_MODEL_SHA256": SHA,
    })

    first = factory.build(
        selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
    )
    second = factory.build(
        selection_key=SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashServiceEpisodeEmbeddingBaseline,
    )

    assert first is second
    assert len(captured) == 1
    assert captured[0].model_path == tmp_path.resolve()
    assert captured[0].model_revision == "commit-one"


def test_bge_m3_load_failure_does_not_fall_back_to_hash(
    tmp_path, monkeypatch,
) -> None:
    baseline_calls = []

    def fail(_config):
        raise BGEM3EmbeddingUnavailable("load failed")

    monkeypatch.setattr(
        "infrastructure.dense_embedding_factory.LocalBGEM3EmbeddingProvider",
        fail,
    )
    factory = DenseEmbeddingProviderFactory({
        KNOWLEDGE_DENSE_EMBEDDING_PROVIDER: "bge_m3",
        "BGE_M3_LOCAL_MODEL_PATH": str(tmp_path),
        "BGE_M3_MODEL_REVISION": "commit-one",
        "BGE_M3_MODEL_SHA256": SHA,
    })

    with pytest.raises(BGEM3EmbeddingUnavailable, match="load failed"):
        factory.build(
            selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
            baseline_factory=lambda: baseline_calls.append(True),
        )

    assert baseline_calls == []


def test_unknown_provider_selection_fails() -> None:
    factory = DenseEmbeddingProviderFactory({
        KNOWLEDGE_DENSE_EMBEDDING_PROVIDER: "unknown",
    })
    with pytest.raises(
        DenseEmbeddingProviderSelectionError,
        match="hash_baseline or bge_m3",
    ):
        factory.build(
            selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
            baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
        )


def test_knowledge_and_service_episode_rollouts_are_independent(
    tmp_path, monkeypatch,
) -> None:
    class FakeProvider:
        def __init__(self, _config) -> None:
            pass

    monkeypatch.setattr(
        "infrastructure.dense_embedding_factory.LocalBGEM3EmbeddingProvider",
        FakeProvider,
    )
    factory = DenseEmbeddingProviderFactory({
        KNOWLEDGE_DENSE_EMBEDDING_PROVIDER: "bge_m3",
        "BGE_M3_LOCAL_MODEL_PATH": str(tmp_path),
        "BGE_M3_MODEL_REVISION": "commit-one",
        "BGE_M3_MODEL_SHA256": SHA,
    })

    knowledge = factory.build(
        selection_key=KNOWLEDGE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashKnowledgeEmbeddingBaseline,
    )
    memory = factory.build(
        selection_key=SERVICE_EPISODE_DENSE_EMBEDDING_PROVIDER,
        baseline_factory=LocalHashServiceEpisodeEmbeddingBaseline,
    )

    assert isinstance(knowledge, FakeProvider)
    assert isinstance(memory, LocalHashServiceEpisodeEmbeddingBaseline)
