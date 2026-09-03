from __future__ import annotations

import json
from types import SimpleNamespace

from application.hybrid_retrieval import (
    DistanceMetric,
    EmbeddingProfile,
    EmbeddingProviderKind,
    GenerationState,
    RetrievalCorpus,
    RetrievalGeneration,
)
from scripts import rebuild_retrieval_generations as cli


def _generation(
    generation_id: str,
    corpus: RetrievalCorpus,
    *,
    model_version: str,
) -> RetrievalGeneration:
    profile = EmbeddingProfile(
        provider="fake-provider",
        provider_kind=EmbeddingProviderKind.MODEL,
        model="fake-bi-encoder",
        model_version=model_version,
        dimension=3,
        model_digest=model_version[-1] * 64,
        document_preprocessing="raw-document-v1",
        query_preprocessing="raw-query-v1",
    )
    return RetrievalGeneration(
        generation_id=generation_id,
        corpus=corpus,
        backend_id="POSTGRES_PG_FTS_ZH_V1",
        backend_fingerprint="fake-backend-v1",
        schema_version="retrieval-v1",
        source_watermark="watermark",
        embedding_model=profile.model,
        embedding_dimension=profile.dimension,
        embedding_model_digest=profile.model_digest,
        distance_metric=DistanceMetric.COSINE,
        vector_extension_version="0.8.6",
        index_method="HNSW",
        index_params_json='{"m":16}',
        chinese_tokenizer="fake-tokenizer-v1",
        lexical_ranker="fake-ranker-v1",
        manifest_hash="a" * 64,
        state=GenerationState.ACTIVE,
        embedding_provider=profile.provider,
        embedding_provider_kind=profile.provider_kind,
        embedding_model_version=profile.model_version,
        embedding_document_preprocessing=profile.document_preprocessing,
        embedding_query_preprocessing=profile.query_preprocessing,
    )


class _KnowledgeOwner:
    def __init__(self, old, new):
        self.old = old
        self.new = new
        self.ensure_calls = 0

    def active_generation(self):
        return self.old

    async def ensure_defaults_async(self):
        self.ensure_calls += 1
        return self.new

    async def doc_count_async(self):
        return 7


class _EpisodeOwner:
    def __init__(self, old, new):
        self.old = old
        self.new = new
        self.generation_ids: list[str] = []

    def active_generation(self):
        return self.old

    def rebuild_and_activate(self, generation_id):
        self.generation_ids.append(generation_id)
        return SimpleNamespace(generation=self.new, episode_count=3)


class _Pool:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_cli_rebuilds_both_generation_owners_and_reports_transition(
    monkeypatch, capsys,
):
    old_knowledge = _generation(
        "knowledge-old", RetrievalCorpus.KNOWLEDGE, model_version="revision-1",
    )
    new_knowledge = _generation(
        "knowledge-new", RetrievalCorpus.KNOWLEDGE, model_version="revision-2",
    )
    old_episode = _generation(
        "episode-old", RetrievalCorpus.SERVICE_EPISODE, model_version="revision-1",
    )
    new_episode = _generation(
        "episode-eval-v2",
        RetrievalCorpus.SERVICE_EPISODE,
        model_version="revision-2",
    )
    knowledge = _KnowledgeOwner(old_knowledge, new_knowledge)
    episode = _EpisodeOwner(old_episode, new_episode)
    pool = _Pool()
    owners = cli._Owners(
        pool=pool,
        knowledge=knowledge,
        service_episode=episode,
        retrieval_runtime=None,
    )
    monkeypatch.setattr(cli, "_compose", lambda _corpus, _env: owners)

    assert cli.main([
        "--corpus", "all", "--generation-id", "episode-eval-v2",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert knowledge.ensure_calls == 1
    assert episode.generation_ids == ["episode-eval-v2"]
    assert pool.closed is True
    assert [(item["old"]["generation_id"], item["new"]["generation_id"])
            for item in payload["results"]] == [
        ("knowledge-old", "knowledge-new"),
        ("episode-old", "episode-eval-v2"),
    ]
    assert [item["count"] for item in payload["results"]] == [7, 3]
    assert payload["results"][0]["new"]["profile"]["model_version"] == (
        "revision-2"
    )
