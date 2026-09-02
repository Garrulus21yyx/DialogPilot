"""M2-T05-A1 KnowledgeRetriever owner and cache compatibility contracts."""
import asyncio

import pytest
from langchain_core.stores import InMemoryByteStore

from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_retriever import (
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
    KnowledgeRetriever,
)
from infrastructure.retrieval_cache import (
    LANGCHAIN_CLASSIC_VERSION,
    RedisRetrievalCache,
    cache_backed_embeddings,
)


SHA = "a" * 64


def _policy(**changes):
    values = {
        "policy_version": "knowledge-legacy-comparison-v1",
        "backend_fingerprint": "LEGACY_BM25_V1",
        "lexical_provider": "LEGACY_BM25_V1",
        "transformer_version": "standalone-v1",
        "embedding_version": "minilm-v1",
        "reranker_version": "listwise-v3",
        "packer_version": "context-packer-v1",
    }
    values.update(changes)
    return KnowledgeRetrievalPolicy(**values)


def _request(policy=None):
    return KnowledgeRetrievalRequest(
        tenant_id="tenant-one", user_scope="user-one",
        authorization_fingerprint="auth-v1",
        acl_policy_fingerprint="acl-v1", deletion_epoch=0,
        requirement_signature="knowledge.active_source",
        query="退款多久到账", history=("我的订单已审核",),
        conversation_range_hash="range-v1", locale="zh-CN", product=None,
        manifest_fingerprint=SHA, generation_id="knowledge-generation-one",
        policy=policy or _policy(),
    )


def _candidate(chunk_id="chunk-one", *, manifest=SHA):
    return {
        "chunk_id": chunk_id, "source_id": "refund-policy",
        "source_revision": "revision-one", "source_checksum": SHA,
        "source_start_char": 0, "source_end_char": 8,
        "content": "审核后原路退回", "title": "退款政策",
        "source_type": "text", "scope": "public",
        "scope_decision": "allowed_public",
        "index_manifest_fingerprint": manifest,
        "ranks": {"raw:bm25": 1, "standalone:vector": 2},
        "score": 0.12,
    }


class _Transformer:
    def __init__(self, *, fail=False):
        self.fail = fail

    async def standalone(self, query, history):
        assert history == ("我的订单已审核",)
        return (query, "rewrite-unavailable") if self.fail else ("退款审核后到账时间", None)


class _Source:
    def __init__(self, rows=None, error=None):
        self.rows = rows if rows is not None else [_candidate()]
        self.error = error
        self.calls = []

    async def search_variants_async(self, variants, *, top_k, retrieval_policy):
        self.calls.append((variants, top_k, retrieval_policy))
        if self.error:
            raise self.error
        return self.rows


class _Reranker:
    def __init__(self, ordered=None):
        self.ordered = ordered

    async def rerank(self, query, candidates):
        ids = tuple(item["chunk_id"] for item in candidates)
        return (self.ordered or ids), False


def test_retriever_owns_legacy_profile_variants_trace_and_canonical_pack():
    source = _Source()
    result = asyncio.run(KnowledgeRetriever(
        candidate_source=source, transformer=_Transformer(),
        reranker=_Reranker(),
    ).retrieve(_request()))

    assert result.status is RetrievalStatus.OK
    assert result.evidence_pack is not None
    assert result.evidence_pack.items[0].chunk_id == "chunk-one"
    assert result.evidence_pack.items[0].source_ref.source_revision == "revision-one"
    assert result.trace is not None
    assert result.trace.variants == (
        ("raw", "退款多久到账", 0.25),
        ("standalone", "退款审核后到账时间", 0.75),
    )
    assert source.calls[0][1] == 20
    assert source.calls[0][2]["policy_fingerprint"] == _policy().fingerprint


def test_rewrite_failure_preserves_all_mass_and_rerank_contract_falls_back():
    second = {**_candidate("two"), "source_id": "shipping-policy"}
    source = _Source([_candidate("one"), second])
    result = asyncio.run(KnowledgeRetriever(
        candidate_source=source, transformer=_Transformer(fail=True),
        reranker=_Reranker(("unknown", "one")),
    ).retrieve(_request()))
    assert result.status is RetrievalStatus.OK
    assert result.trace is not None
    assert result.trace.variants == (("raw", "退款多久到账", 1.0),)
    assert result.trace.rewrite_fallback is True
    assert result.trace.rerank_fallback is True
    assert tuple(item.chunk_id for item in result.evidence_pack.items) == ("one", "two")


@pytest.mark.parametrize(
    ("source", "status", "detail"),
    [
        (_Source([]), RetrievalStatus.NO_EVIDENCE, "NO_AUTHORIZED_CANDIDATES"),
        (_Source(error=RuntimeError("down")), RetrievalStatus.UNAVAILABLE,
         "CANDIDATE_SOURCE_UNAVAILABLE"),
        (_Source([_candidate(manifest="b" * 64)]), RetrievalStatus.INVALID_CONTRACT,
         "CANDIDATE_PROVENANCE_INVALID"),
        (_Source([_candidate("same"), _candidate("same")]), RetrievalStatus.CONFLICT,
         "DUPLICATE_CANDIDATE_ID"),
    ],
)
def test_status_algebra_never_carries_partial_or_diagnostic_evidence(
    source, status, detail,
):
    result = asyncio.run(KnowledgeRetriever(
        candidate_source=source, transformer=_Transformer(),
        reranker=_Reranker(),
    ).retrieve(_request()))
    assert (result.status, result.detail_code) == (status, detail)
    assert result.evidence_pack is None


def test_policy_fingerprint_changes_for_every_owned_parameter():
    baseline = _policy()
    changes = {
        "raw_query_weight": 0.3, "standalone_query_weight": 0.7,
        "dense_weight": 0.3, "lexical_weight": 0.7, "rrf_k": 11,
        "candidate_k": 21, "final_k": 4, "context_max_tokens": 2500,
        "backend_fingerprint": "PG_FTS_ZH_V1",
        "transformer_version": "standalone-v2",
        "embedding_version": "minilm-v2",
        "reranker_version": "listwise-v4",
        "packer_version": "context-packer-v2",
    }
    for field, value in changes.items():
        candidate = _policy(**{field: value})
        assert candidate.fingerprint != baseline.fingerprint, field


class _Embeddings:
    def __init__(self):
        self.query_calls = 0
        self.document_calls = 0

    def embed_query(self, text):
        self.query_calls += 1
        return [float(len(text)), 1.0]

    def embed_documents(self, texts):
        self.document_calls += 1
        return [[float(len(text)), 1.0] for text in texts]


def test_pinned_langchain_cache_backed_embeddings_import_and_query_cache():
    import importlib.metadata

    assert importlib.metadata.version("langchain-classic") == LANGCHAIN_CLASSIC_VERSION
    underlying = _Embeddings()
    store = InMemoryByteStore()
    cached = cache_backed_embeddings(
        underlying, document_store=store, query_store=store,
    )
    assert cached.embed_query("refund") == cached.embed_query("refund")
    assert underlying.query_calls == 1
    assert cached.embed_documents(["policy"]) == cached.embed_documents(["policy"])
    assert underlying.document_calls == 1


class _BrokenRedis:
    def get(self, _key):
        import redis
        raise redis.ConnectionError("down")

    def set(self, *_args, **_kwargs):
        import redis
        raise redis.ConnectionError("down")

    def delete(self, _key):
        import redis
        raise redis.ConnectionError("down")


def test_redis_failure_is_cache_miss_not_retrieval_status():
    cache = RedisRetrievalCache(_BrokenRedis())
    assert cache.get("candidate-key") is None
    assert cache.set("candidate-key", b"value", ttl_seconds=30) is False
    assert cache.delete("candidate-key") is False
