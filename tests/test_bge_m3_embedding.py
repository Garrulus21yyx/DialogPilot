"""Offline BGE-M3 provider preserves one truthful document/query profile."""
from __future__ import annotations

import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProviderKind,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.bge_m3_embedding import (
    BGE_M3_DIMENSION,
    BGE_M3_MODEL_ID,
    BGE_M3_PREPROCESSING,
    BGE_M3_PROVIDER_ID,
    BGEM3EmbeddingConfig,
    BGEM3EmbeddingConfigurationError,
    BGEM3EmbeddingUnavailable,
    LocalBGEM3EmbeddingProvider,
    _load_local_sentence_transformer,
)
from infrastructure.knowledge_embedding import (
    KnowledgeDocumentEmbedder,
    KnowledgeQueryEmbedder,
)


SHA = "a" * 64


class FakeVectors(list):
    def tolist(self):
        return list(self)


class FakeModel:
    def __init__(self, *, dimension=BGE_M3_DIMENSION):
        self.dimension = dimension
        self.calls = []

    def get_sentence_embedding_dimension(self):
        return self.dimension

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return FakeVectors([
            [1.0] + [0.0] * (BGE_M3_DIMENSION - 1) for _ in texts
        ])


def config(tmp_path, **changes):
    model_path = tmp_path / "bge-m3"
    model_path.mkdir(exist_ok=True)
    values = {
        "model_path": model_path,
        "model_revision": "commit-0123456789abcdef",
        "model_digest": SHA,
        "device": "cpu",
        "batch_size": 8,
    }
    values.update(changes)
    return BGEM3EmbeddingConfig(**values)


def generation(profile):
    return RetrievalGeneration(
        generation_id="knowledge-bge-m3-generation-1",
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint="postgres-knowledge-v1",
        schema_version="retrieval-v1",
        source_watermark="source-watermark-1",
        embedding_model=profile.model,
        embedding_dimension=profile.dimension,
        embedding_model_digest=profile.model_digest,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash="b" * 64,
        embedding_provider=profile.provider,
        embedding_provider_kind=profile.provider_kind,
        embedding_model_version=profile.model_version,
        embedding_document_preprocessing=profile.document_preprocessing,
        embedding_query_preprocessing=profile.query_preprocessing,
    )


def test_provider_exposes_truthful_model_profile_and_preserves_raw_inputs(tmp_path):
    model = FakeModel()
    provider = LocalBGEM3EmbeddingProvider(
        config(tmp_path), model_loader=lambda _config: model,
    )
    source_text = "退款 ABC-123 将在 3 天内到账。"
    query_text = "Refund ABC-123 到账了吗？"

    documents = KnowledgeDocumentEmbedder(provider)((source_text,))
    query = KnowledgeQueryEmbedder(provider)(
        query_text, generation(provider.profile),
    )

    assert len(documents[0]) == BGE_M3_DIMENSION
    assert len(query) == BGE_M3_DIMENSION
    assert [call[0] for call in model.calls] == [[source_text], [query_text]]
    assert all(call[1]["normalize_embeddings"] is True for call in model.calls)
    assert all(call[1]["batch_size"] == 8 for call in model.calls)
    assert provider.profile.provider_kind is EmbeddingProviderKind.MODEL
    assert provider.profile.provider == BGE_M3_PROVIDER_ID
    assert provider.profile.model == BGE_M3_MODEL_ID
    assert provider.profile.model_version == "commit-0123456789abcdef"
    assert provider.profile.model_digest == SHA
    assert provider.profile.dimension == BGE_M3_DIMENSION
    assert provider.profile.document_preprocessing == BGE_M3_PREPROCESSING
    assert provider.profile.query_preprocessing == BGE_M3_PREPROCESSING


def test_default_loader_can_only_open_a_local_non_remote_code_artifact(
    tmp_path, monkeypatch,
):
    captured = {}

    def sentence_transformer(path, **kwargs):
        captured.update(path=path, **kwargs)
        return FakeModel()

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=sentence_transformer),
    )
    value = config(tmp_path, device="cuda:0")

    assert _load_local_sentence_transformer(value).dimension == BGE_M3_DIMENSION
    assert captured == {
        "path": str(value.model_path),
        "device": "cuda:0",
        "trust_remote_code": False,
        "local_files_only": True,
    }


def test_config_from_env_requires_local_artifact_revision_and_digest(tmp_path):
    model_path = tmp_path / "bge-m3"
    model_path.mkdir()

    value = BGEM3EmbeddingConfig.from_env({
        "BGE_M3_LOCAL_MODEL_PATH": str(model_path),
        "BGE_M3_MODEL_REVISION": "commit-1",
        "BGE_M3_MODEL_SHA256": SHA,
        "BGE_M3_DEVICE": "cuda:1",
        "BGE_M3_BATCH_SIZE": "16",
    })

    assert value.model_path == model_path.resolve()
    assert value.model_revision == "commit-1"
    assert value.model_digest == SHA
    assert value.device == "cuda:1"
    assert value.batch_size == 16


@pytest.mark.parametrize(
    "values, message",
    [
        ({}, "BGE_M3_LOCAL_MODEL_PATH"),
        ({"BGE_M3_LOCAL_MODEL_PATH": "BAAI/bge-m3"}, "MODEL_REVISION"),
    ],
)
def test_config_from_env_fails_closed_when_identity_is_incomplete(values, message):
    with pytest.raises(BGEM3EmbeddingConfigurationError, match=message):
        BGEM3EmbeddingConfig.from_env(values)


def test_config_rejects_a_missing_local_artifact_or_unpinned_digest(tmp_path):
    with pytest.raises(BGEM3EmbeddingConfigurationError, match="local directory"):
        BGEM3EmbeddingConfig(
            tmp_path / "missing-bge-m3", "commit-1", SHA,
        )

    with pytest.raises(BGEM3EmbeddingConfigurationError, match="SHA-256"):
        config(tmp_path, model_digest="latest")


def test_provider_rejects_an_artifact_with_non_bge_dimension(tmp_path):
    with pytest.raises(BGEM3EmbeddingConfigurationError, match="1024d"):
        LocalBGEM3EmbeddingProvider(
            config(tmp_path), model_loader=lambda _config: FakeModel(dimension=768),
        )


def test_model_load_failure_is_typed_and_never_falls_back_to_hash(tmp_path):
    def unavailable(_config):
        raise OSError("local weights unavailable")

    with pytest.raises(BGEM3EmbeddingUnavailable, match="could not be loaded"):
        LocalBGEM3EmbeddingProvider(
            config(tmp_path), model_loader=unavailable,
        )


def test_runtime_path_is_not_generation_identity_but_revision_and_digest_are(
    tmp_path,
):
    first_path = tmp_path / "deployment-a"
    second_path = tmp_path / "deployment-b"
    first_path.mkdir()
    second_path.mkdir()
    first = LocalBGEM3EmbeddingProvider(
        BGEM3EmbeddingConfig(first_path, "commit-1", SHA),
        model_loader=lambda _config: FakeModel(),
    )
    second = LocalBGEM3EmbeddingProvider(
        BGEM3EmbeddingConfig(second_path, "commit-1", SHA),
        model_loader=lambda _config: FakeModel(),
    )
    changed = LocalBGEM3EmbeddingProvider(
        BGEM3EmbeddingConfig(second_path, "commit-2", "b" * 64),
        model_loader=lambda _config: FakeModel(),
    )

    assert first.profile == second.profile
    assert first.profile.fingerprint == second.profile.fingerprint
    assert first.profile.fingerprint != changed.profile.fingerprint


def test_query_generation_rejects_profile_drift_before_model_call(tmp_path):
    model = FakeModel()
    provider = LocalBGEM3EmbeddingProvider(
        config(tmp_path), model_loader=lambda _config: model,
    )
    mismatched = replace(
        generation(provider.profile),
        embedding_model_version="different-commit",
    )

    with pytest.raises(ValueError, match="profile differs"):
        KnowledgeQueryEmbedder(provider)("退款状态", mismatched)
    assert model.calls == []
