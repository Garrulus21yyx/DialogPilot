"""Knowledge document/query embedding share one immutable provider profile."""
from dataclasses import replace

import pytest

from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    EmbeddingProviderKind,
    RetrievalContractError,
    RetrievalCorpus,
    RetrievalGeneration,
)
from infrastructure.knowledge_embedding import (
    KnowledgeDocumentEmbedder,
    KnowledgeEmbeddingContractError,
    KnowledgeQueryEmbedder,
)


SHA = "a" * 64


def _profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        provider="test-model-provider",
        provider_kind=EmbeddingProviderKind.MODEL,
        model="test-multilingual-bi-encoder",
        model_version="revision-abc",
        dimension=3,
        model_digest=SHA,
        document_preprocessing="raw-document-prefix-v1",
        query_preprocessing="raw-query-prefix-v1",
    )


def _generation(profile: EmbeddingProfile | None = None) -> RetrievalGeneration:
    value = profile or _profile()
    return RetrievalGeneration(
        generation_id="knowledge-generation-1",
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint="postgres-knowledge-v1",
        schema_version="retrieval-v1",
        source_watermark="source-watermark-1",
        embedding_model=value.model,
        embedding_dimension=value.dimension,
        embedding_model_digest=value.model_digest,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="EXACT",
        index_params_json="{}",
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash="b" * 64,
        embedding_provider=value.provider,
        embedding_provider_kind=value.provider_kind,
        embedding_model_version=value.model_version,
        embedding_document_preprocessing=value.document_preprocessing,
        embedding_query_preprocessing=value.query_preprocessing,
    )


class Provider:
    def __init__(self, profile=None, *, document_vectors=None, query_vectors=None):
        self.profile = profile or _profile()
        self.document_vectors = (
            [[1.0, 0.0, 0.0]]
            if document_vectors is None else document_vectors
        )
        self.query_vectors = (
            [[1.0, 0.0, 0.0]] if query_vectors is None else query_vectors
        )
        self.document_calls = []
        self.query_calls = []

    def embed_documents(self, raw_source_chunks):
        self.document_calls.append(tuple(raw_source_chunks))
        return self.document_vectors

    def embed_queries(self, raw_queries):
        self.query_calls.append(tuple(raw_queries))
        return self.query_vectors


def test_embedder_rejects_missing_or_incomplete_provider_metadata():
    incomplete = Provider(_profile())
    incomplete.profile = {"model": "not-an-embedding-profile"}

    with pytest.raises(KnowledgeEmbeddingContractError, match="metadata"):
        KnowledgeDocumentEmbedder(incomplete)


def test_profile_requires_a_version_digest():
    with pytest.raises(RetrievalContractError):
        replace(_profile(), model_digest="not-a-digest")


@pytest.mark.parametrize("vectors", [
    [],
    [[1.0, 0.0]],
    [[1.0, float("nan"), 0.0]],
    [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
])
def test_document_embedder_rejects_provider_batch_dimension_or_numeric_drift(vectors):
    provider = Provider(document_vectors=vectors)
    with pytest.raises(KnowledgeEmbeddingContractError, match="provider"):
        KnowledgeDocumentEmbedder(provider)(("原始退款文本",))


@pytest.mark.parametrize("changes", [
    {"embedding_provider": "different-provider"},
    {
        "embedding_provider": "different-hash-provider",
        "embedding_provider_kind": EmbeddingProviderKind.HASH_BASELINE,
        "embedding_model": "different-hash-model",
    },
    {"embedding_model": "different-model"},
    {"embedding_model_version": "different-revision"},
    {"embedding_dimension": 4},
    {"embedding_model_digest": "c" * 64},
    {"embedding_document_preprocessing": "different-document-preprocessing"},
    {"embedding_query_preprocessing": "different-query-preprocessing"},
])
def test_query_embedder_rejects_any_generation_profile_drift_before_provider(
    changes,
):
    provider = Provider()
    generation = replace(_generation(), **changes)

    with pytest.raises(KnowledgeEmbeddingContractError, match="profile differs"):
        KnowledgeQueryEmbedder(provider)("原始查询", generation)
    assert provider.query_calls == []


def test_document_and_query_adapters_forward_raw_text_without_lexical_projection():
    provider = Provider()
    document = "退款ABC 将在 3 天内到账。"
    query = "退款ABC 到账了吗？"

    assert KnowledgeDocumentEmbedder(provider)((document,)) == (
        (1.0, 0.0, 0.0),
    )
    assert KnowledgeQueryEmbedder(provider)(query, _generation()) == (
        1.0, 0.0, 0.0,
    )
    assert provider.document_calls == [(document,)]
    assert provider.query_calls == [(query,)]


def test_embedding_profile_is_part_of_generation_immutable_identity():
    first = _generation()
    changed = replace(
        first, embedding_query_preprocessing="raw-query-prefix-v2",
    )

    assert first.embedding_profile.fingerprint != changed.embedding_profile.fingerprint
    assert first.immutable_fingerprint() != changed.immutable_fingerprint()


def test_legacy_generation_is_not_claimed_replayable_with_incomplete_metadata():
    legacy = replace(
        _generation(),
        embedding_provider="legacy-unrecorded",
        embedding_provider_kind=EmbeddingProviderKind.LEGACY_UNSPECIFIED,
        embedding_model_version="legacy-unrecorded",
        embedding_document_preprocessing="legacy-unrecorded",
        embedding_query_preprocessing="legacy-unrecorded",
    )

    assert legacy.embedding_model_digest == SHA
    assert legacy.embedding_metadata_complete is False
    assert legacy.replayable_across_environments is False
