"""M2-PF01 backend-neutral retrieval and generation state contracts."""
import pytest

from application.hybrid_retrieval import (
    DistanceMetric,
    EpisodeSearchScope,
    GenerationConflict,
    GenerationState,
    HybridRetrievalRequest,
    HybridRetrievalResult,
    InMemoryRetrievalGenerationRegistry,
    KnowledgeSearchScope,
    RetrievalCandidate,
    RetrievalContractError,
    RetrievalCorpus,
    RetrievalGeneration,
    RetrievalStatus,
)


SHA = "a" * 64


def _generation(
    suffix: str = "one",
    *,
    corpus: RetrievalCorpus = RetrievalCorpus.KNOWLEDGE,
) -> RetrievalGeneration:
    return RetrievalGeneration(
        generation_id=f"generation-{suffix}",
        corpus=corpus,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint=f"backend-{suffix}",
        schema_version="retrieval-v1",
        source_watermark="source-revision-10",
        embedding_model="all-MiniLM-L6-v2",
        embedding_dimension=384,
        embedding_model_digest="UNVERIFIED:legacy-model-weight-digest",
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"ef_construction":64,"m":16}',
        chinese_tokenizer="ascii-cjk-unigram-bigram-v1",
        lexical_ranker="PG_FTS_ZH_V1",
        manifest_hash=SHA,
    )


def test_request_scope_is_corpus_typed_and_pins_backend_generation_policy():
    request = HybridRetrievalRequest(
        tenant_id="tenant-a",
        corpus=RetrievalCorpus.KNOWLEDGE,
        backend_fingerprint="pg-fts-v1",
        generation_id="generation-one",
        policy_fingerprint="knowledge-policy-v1",
        query_text="退款 流程",
        query_embedding=(0.1, 0.2),
        scope=KnowledgeSearchScope("public", "zh-CN", "payments"),
    )
    assert request.scope.locale == "zh-CN"
    with pytest.raises(RetrievalContractError, match="knowledge scope"):
        HybridRetrievalRequest(**{
            **request.__dict__, "scope": EpisodeSearchScope("user-a"),
        })


@pytest.mark.parametrize("status", [
    RetrievalStatus.NO_EVIDENCE,
    RetrievalStatus.UNAVAILABLE,
    RetrievalStatus.INVALID_CONTRACT,
    RetrievalStatus.CONFLICT,
])
def test_non_candidate_statuses_cannot_leak_partial_results(status):
    candidate = RetrievalCandidate(
        candidate_id="candidate-a",
        corpus=RetrievalCorpus.KNOWLEDGE,
        generation_id="generation-one",
        source_id="source-a",
        source_revision="revision-a",
        rank=1,
        score=0.5,
        provenance_sha256=SHA,
    )
    with pytest.raises(RetrievalContractError, match="partial candidates"):
        HybridRetrievalResult(
            status=status,
            backend_fingerprint="backend-a",
            generation_id="generation-one",
            dense_candidates=(candidate,),
        )


def test_candidate_routes_preserve_source_rank_without_fusion_contract():
    candidates = tuple(
        RetrievalCandidate(
            candidate_id=f"candidate-{rank}",
            corpus=RetrievalCorpus.KNOWLEDGE,
            generation_id="generation-one",
            source_id=f"source-{rank}",
            source_revision="revision-a",
            rank=rank,
            score=1 / rank,
            provenance_sha256=SHA,
        )
        for rank in (1, 2)
    )
    result = HybridRetrievalResult(
        RetrievalStatus.OK, "backend-a", "generation-one",
        dense_candidates=candidates,
        lexical_candidates=(
            RetrievalCandidate(**{
                **candidates[1].__dict__, "rank": 1,
            }),
            RetrievalCandidate(**{
                **candidates[0].__dict__, "rank": 2,
            }),
        ),
    )
    assert [item.candidate_id for item in result.dense_candidates] == [
        "candidate-1", "candidate-2",
    ]
    with pytest.raises(RetrievalContractError, match="source ranks"):
        HybridRetrievalResult(
            RetrievalStatus.OK, "backend-a", "generation-one",
            lexical_candidates=tuple(reversed(candidates)),
        )


def test_generation_state_machine_is_immutable_cas_and_corpus_scoped():
    registry = InMemoryRetrievalGenerationRegistry()
    first = registry.register(_generation())
    assert first.replayable_across_environments is False
    assert registry.register(first) == first
    with pytest.raises(GenerationConflict, match="immutable"):
        registry.register(RetrievalGeneration(**{
            **first.__dict__, "embedding_dimension": 768,
        }))
    with pytest.raises(GenerationConflict, match="only READY"):
        registry.activate(first.generation_id, expected_version=0)
    registry.transition(first.generation_id, GenerationState.BUILDING)
    registry.transition(first.generation_id, GenerationState.READY)
    pointer = registry.activate(first.generation_id, expected_version=0)
    assert pointer.previous_generation_id is None
    assert pointer.version == 1

    episode = registry.register(_generation(
        "episode", corpus=RetrievalCorpus.SERVICE_EPISODE,
    ))
    registry.transition(episode.generation_id, GenerationState.BUILDING)
    registry.transition(episode.generation_id, GenerationState.READY)
    episode_pointer = registry.activate(episode.generation_id, expected_version=0)
    assert episode_pointer.version == 1
    assert registry.pointer(RetrievalCorpus.KNOWLEDGE, first.backend_id) != (
        episode_pointer
    )
