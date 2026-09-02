"""M2-T05-A1 KnowledgeRetriever owner and cache compatibility contracts."""
import asyncio
from dataclasses import replace

import pytest
from langchain_core.stores import InMemoryByteStore

from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_retriever import (
    KnowledgeRetrievalPolicy,
    KnowledgeRetrievalRequest,
    KnowledgeRetriever,
    RetrievalCacheKeyBuilder,
)
from infrastructure.retrieval_cache import (
    LANGCHAIN_CLASSIC_VERSION,
    RedisRetrievalCache,
    cache_backed_embeddings,
    embedding_cache_stores,
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


def _request(policy=None, **changes):
    value = KnowledgeRetrievalRequest(
        tenant_id="tenant-one", user_scope="user-one",
        authorization_fingerprint="auth-v1",
        acl_policy_fingerprint="acl-v1", deletion_epoch=0,
        requirement_signature="knowledge.active_source",
        query="退款多久到账", history=("我的订单已审核",),
        conversation_range_hash="range-v1", locale="zh-CN", product=None,
        manifest_fingerprint=SHA, generation_id="knowledge-generation-one",
        policy=policy or _policy(),
    )
    return replace(value, **changes)


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
        self.calls = 0

    async def standalone(self, query, history):
        self.calls += 1
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
        self.calls = 0

    async def rerank(self, query, candidates):
        self.calls += 1
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
        (_Source([
            _candidate("old"),
            {**_candidate("new"), "source_revision": "revision-two"},
        ]), RetrievalStatus.CONFLICT, "ACTIVE_SOURCE_REVISION_CONFLICT"),
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


class _MemoryCache:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, *, ttl_seconds):
        assert 300 <= ttl_seconds <= 360
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)
        return True


class _SingleFlightMemoryCache(_MemoryCache):
    def __init__(self):
        super().__init__()
        self.leases = {}

    def acquire(self, key, token, *, lease_seconds):
        if key in self.leases:
            return False
        self.leases[key] = token
        return True

    def release(self, key, token):
        if self.leases.get(key) != token:
            return False
        del self.leases[key]
        return True


class _Validator:
    def __init__(self, valid=True):
        self.valid = valid
        self.calls = 0

    def validate(self, pack, request):
        self.calls += 1
        return (
            self.valid
            and pack.index_manifest_fingerprint == request.manifest_fingerprint
            and all(item.source_ref.source_revision == "revision-one" for item in pack.items)
        )

    def validate_candidates(self, candidates, request):
        return self.valid and all(
            item["index_manifest_fingerprint"] == request.manifest_fingerprint
            and item["scope"] == "public"
            for item in candidates
        )


def test_exact_layer_cache_hit_and_forced_recompute_are_evidence_equivalent():
    cache = _MemoryCache()
    transformer, source, reranker = _Transformer(), _Source(), _Reranker()
    validator = _Validator()
    retriever = KnowledgeRetriever(
        candidate_source=source, transformer=transformer, reranker=reranker,
        cache=cache, evidence_validator=validator,
    )
    first = asyncio.run(retriever.retrieve(_request()))
    second = asyncio.run(retriever.retrieve(_request()))
    forced = asyncio.run(retriever.retrieve(_request(force_recompute=True)))
    def stable(result):
        return tuple(item.chunk_id for item in result.evidence_pack.items)
    assert stable(first) == stable(second) == stable(forced) == ("chunk-one",)
    assert second.trace.cache_hits == (
        "transform", "candidates", "rerank", "evidence-pack",
    )
    assert transformer.calls == 2
    assert len(source.calls) == 2
    assert reranker.calls == 2
    assert validator.calls == 1


def test_concurrent_candidate_miss_uses_single_flight_without_semantic_change():
    class SlowSource(_Source):
        async def search_variants_async(self, variants, *, top_k, retrieval_policy):
            self.calls.append((variants, top_k, retrieval_policy))
            await asyncio.sleep(0.04)
            return self.rows

    async def run():
        source = SlowSource()
        retriever = KnowledgeRetriever(
            candidate_source=source, transformer=_Transformer(),
            reranker=_Reranker(), cache=_SingleFlightMemoryCache(),
            evidence_validator=_Validator(),
        )
        results = await asyncio.gather(
            retriever.retrieve(_request()), retriever.retrieve(_request()),
        )
        return source, results

    source, results = asyncio.run(run())
    assert len(source.calls) == 1
    assert [result.status for result in results] == [
        RetrievalStatus.OK, RetrievalStatus.OK,
    ]
    assert [result.evidence_pack.items[0].chunk_id for result in results] == [
        "chunk-one", "chunk-one",
    ]


def test_stale_pack_cache_is_not_reused_when_owner_validation_fails():
    cache = _MemoryCache()
    validator = _Validator(valid=True)
    retriever = KnowledgeRetriever(
        candidate_source=_Source(), transformer=_Transformer(),
        reranker=_Reranker(), cache=cache, evidence_validator=validator,
    )
    asyncio.run(retriever.retrieve(_request()))
    validator.valid = False
    result = asyncio.run(retriever.retrieve(_request()))
    assert result.status is RetrievalStatus.OK
    assert "evidence-pack" not in result.trace.cache_hits
    assert validator.calls == 1


def test_stale_candidate_cache_cannot_hide_backend_unavailable():
    cache = _MemoryCache()
    validator = _Validator(valid=True)
    source = _Source()
    retriever = KnowledgeRetriever(
        candidate_source=source, transformer=_Transformer(),
        reranker=_Reranker(), cache=cache, evidence_validator=validator,
    )
    assert asyncio.run(retriever.retrieve(_request())).status is RetrievalStatus.OK
    validator.valid = False
    source.error = RuntimeError("backend down after generation changed")
    result = asyncio.run(retriever.retrieve(_request()))
    assert result.status is RetrievalStatus.UNAVAILABLE
    assert result.evidence_pack is None


def test_layer_keys_change_only_at_or_after_the_owned_input_boundary():
    request = _request()
    variants = (("raw", request.query, 1.0),)
    candidate = KnowledgeRetriever._candidate(_candidate(), request)
    keys = (
        RetrievalCacheKeyBuilder.transform(request),
        RetrievalCacheKeyBuilder.embedding(request, normalized_text=request.query),
        RetrievalCacheKeyBuilder.candidates(request, variants),
        RetrievalCacheKeyBuilder.rerank(request, (candidate,)),
        RetrievalCacheKeyBuilder.evidence_pack(
            request, variants=variants, candidates=(candidate,),
            ordered_ids=(candidate.chunk_id,),
        ),
    )
    changed = _request(manifest_fingerprint="b" * 64)
    changed_keys = (
        RetrievalCacheKeyBuilder.transform(changed),
        RetrievalCacheKeyBuilder.embedding(changed, normalized_text=changed.query),
        RetrievalCacheKeyBuilder.candidates(changed, variants),
        RetrievalCacheKeyBuilder.rerank(changed, (candidate,)),
        RetrievalCacheKeyBuilder.evidence_pack(
            changed, variants=variants, candidates=(candidate,),
            ordered_ids=(candidate.chunk_id,),
        ),
    )
    assert changed_keys[:2] == keys[:2]
    assert changed_keys[2] != keys[2]
    assert changed_keys[3] == keys[3]
    assert changed_keys[4] != keys[4]

    deleted = _request(deletion_epoch=1)
    deleted_keys = (
        RetrievalCacheKeyBuilder.transform(deleted),
        RetrievalCacheKeyBuilder.embedding(deleted, normalized_text=deleted.query),
        RetrievalCacheKeyBuilder.candidates(deleted, variants),
        RetrievalCacheKeyBuilder.rerank(deleted, (candidate,)),
        RetrievalCacheKeyBuilder.evidence_pack(
            deleted, variants=variants, candidates=(candidate,),
            ordered_ids=(candidate.chunk_id,),
        ),
    )
    assert deleted_keys[0] == keys[0]
    assert deleted_keys[1] != keys[1]
    assert deleted_keys[2] != keys[2]
    assert deleted_keys[3] == keys[3]
    assert deleted_keys[4] != keys[4]


class _LeaseRedis:
    def __init__(self):
        self.value = None

    def set(self, _key, value, **kwargs):
        if kwargs.get("nx") and self.value is not None:
            return False
        self.value = value
        return True

    def eval(self, _script, _count, _key, token):
        if self.value != token:
            return 0
        self.value = None
        return 1


def test_redis_single_flight_lease_is_owner_token_fenced():
    cache = RedisRetrievalCache(_LeaseRedis())
    assert cache.acquire("embedding-key", "owner-one") is True
    assert cache.acquire("embedding-key", "owner-two") is False
    assert cache.release("embedding-key", "owner-two") is False
    assert cache.release("embedding-key", "owner-one") is True


def test_embedding_namespaces_isolate_subject_epoch_and_require_shared_approval():
    client = _LeaseRedis()
    doc, query = embedding_cache_stores(
        client, tenant_id="tenant-one", user_scope="user-one",
        deletion_epoch=4, embedding_fingerprint="minilm-v1",
    )
    assert "tenant-one:user-one:e4" in doc.prefix
    assert "tenant-one:user-one:e4" in query.prefix
    shared, _ = embedding_cache_stores(
        client, tenant_id="tenant-one", user_scope="user-one",
        deletion_epoch=4, embedding_fingerprint="minilm-v1",
        source_corpus_scope="knowledge-public", shared_source_approved=True,
    )
    assert "tenant-one:knowledge-public" in shared.prefix
    assert "user-one" not in shared.prefix
    with pytest.raises(ValueError, match="not approved"):
        embedding_cache_stores(
            client, tenant_id="tenant-one", user_scope="user-one",
            deletion_epoch=4, embedding_fingerprint="minilm-v1",
            source_corpus_scope="knowledge-public",
        )
