"""知识库来源 ID、Token overlap 切片与混合召回合同。"""
import random

import pytest

from memory.hybrid_retrieval import HybridMemoryRetriever, MemoryDocument
from memory.context import TokenEstimator
from mcp.document_chunker import ChunkStrategy
from mcp.knowledge_base import IncompatibleKnowledgeIndexError, KnowledgeBase
from mcp.source_document import SourceDocument, SourceDocumentContractError
from mcp.sparse_index import PersistentBM25Index, SparseDocument


class FakeCollection:
    def __init__(self):
        self.ids = []
        self.documents = []
        self.metadatas = []
        self.get_calls = []

    def count(self):
        return len(self.ids)

    def add(self, *, ids, documents, metadatas):
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)

    def query(self, **kwargs):
        order = [
            index for index in reversed(range(len(self.ids)))
            if not kwargs.get("where") or all(
                self.metadatas[index].get(key) == value
                for key, value in kwargs["where"].items()
            )
        ][:kwargs.get("n_results", len(self.ids))]
        return {
            "ids": [[self.ids[index] for index in order]],
            "documents": [[self.documents[index] for index in order]],
            "metadatas": [[self.metadatas[index] for index in order]],
            "distances": [[float(index) for index in order]],
        }

    def get(self, **kwargs):
        self.get_calls.append(dict(kwargs))
        selected = set(kwargs.get("ids") or self.ids)
        indexes = [
            index for index, stored_id in enumerate(self.ids)
            if stored_id in selected and (
                not kwargs.get("where") or all(
                    self.metadatas[index].get(key) == value
                    for key, value in kwargs["where"].items()
                )
            )
        ]
        return {
            "ids": [self.ids[index] for index in indexes],
            "documents": [self.documents[index] for index in indexes],
            "metadatas": [self.metadatas[index] for index in indexes],
        }


def bare_knowledge_base(*, max_tokens=512, overlap_tokens=64):
    knowledge_base = KnowledgeBase.__new__(KnowledgeBase)
    knowledge_base._collection = FakeCollection()
    knowledge_base._hybrid_retriever = HybridMemoryRetriever(recency_weight=0.0)
    knowledge_base._token_estimator = TokenEstimator()
    knowledge_base._chunk_max_tokens = max_tokens
    knowledge_base._chunk_overlap_tokens = overlap_tokens
    knowledge_base._chunk_strategy = ChunkStrategy.FIXED_TOKENS
    knowledge_base._sparse_index = PersistentBM25Index(":memory:")
    return knowledge_base


def test_knowledge_base_preserves_source_document_ids_and_returns_rank_evidence():
    knowledge_base = bare_knowledge_base()
    inserted = knowledge_base.add_documents([
        {"id": "kb-target", "title": "登录", "content": "登录错误 E401 表示令牌过期。"},
        {"id": "kb-noise", "title": "配送", "content": "配送通常需要三天。"},
    ])

    hits = knowledge_base.search("E401 令牌", top_k=2)

    assert inserted == 2
    assert hits[0]["document_id"] == "kb-target"
    assert hits[0]["chunk_id"] == "kb-target::chunk-0"
    assert "raw:bm25" in hits[0]["sources"]
    assert "recency" not in hits[0]["sources"]
    assert knowledge_base._collection.metadatas[0]["document_id"] == "kb-target"
    assert knowledge_base._collection.metadatas[0]["source_id"] == "kb-target"
    assert knowledge_base._collection.metadatas[0]["source_type"] == "text"
    assert knowledge_base._collection.metadatas[0]["scope"] == "public"
    assert len(knowledge_base._collection.metadatas[0]["source_checksum"]) == 64
    assert knowledge_base._collection.metadatas[0]["chunking_version"] == 4
    assert knowledge_base._collection.metadatas[0]["chunk_strategy"] == "fixed_tokens"
    assert knowledge_base._collection.metadatas[0]["source_start_char"] == 0
    assert knowledge_base._collection.metadatas[0]["source_end_char"] == len("登录错误 E401 表示令牌过期。")


def test_source_document_contract_is_stable_public_and_checksum_verified():
    first = SourceDocument.create(title="退款政策", content="七天内可申请。", source_type="md")
    second = SourceDocument.create(title="退款政策", content="七天内可申请。", source_type="markdown")

    assert first == second
    assert first.source_id.startswith("source-")
    assert first.scope == "public"
    assert first.source_type == "markdown"
    with pytest.raises(SourceDocumentContractError, match="checksum mismatch"):
        SourceDocument.create(
            source_id="refund-policy", title="退款政策", content="七天内可申请。",
            checksum="0" * 64,
        )
    with pytest.raises(SourceDocumentContractError, match="scope=public"):
        SourceDocument.from_mapping({
            "title": "内部手册", "content": "敏感内容", "scope": "internal",
        })


def test_persistent_sparse_index_reopens_without_retokenizing_corpus(tmp_path):
    path = tmp_path / "sparse.db"
    rows = [
        SparseDocument("refund", "退款期限为七天，错误码 R-7。"),
        SparseDocument("delivery", "配送需要三天。"),
    ]
    first = PersistentBM25Index(str(path))
    assert first.ensure(rows, corpus_fingerprint="corpus-v1") is True
    assert first.search("R-7 退款", top_k=2)[0] == "refund"
    first.close()

    reopened = PersistentBM25Index(str(path))
    assert reopened.ensure(rows, corpus_fingerprint="corpus-v1") is False
    assert reopened.search("配送", top_k=1) == ["delivery"]
    with reopened._connection:
        reopened._connection.execute(
            "UPDATE sparse_metadata SET value='broken' WHERE key='tokenizer_version'"
        )
    assert reopened.ensure(rows, corpus_fingerprint="corpus-v1") is True
    assert reopened.manifest["tokenizer_version"] == PersistentBM25Index.TOKENIZER_VERSION


def test_persistent_sparse_ranking_matches_reference_bm25_for_seeded_corpora():
    rng = random.Random(20260901)
    vocabulary = ["退款", "配送", "E401", "R-7", "审核", "到账", "会员", "不是"]
    for _ in range(80):
        documents = [
            SparseDocument(
                f"doc-{index}",
                " ".join(rng.choice(vocabulary) for _ in range(rng.randint(4, 30))),
            )
            for index in range(rng.randint(2, 25))
        ]
        query = " ".join(rng.choice(vocabulary) for _ in range(rng.randint(1, 4)))
        sparse = PersistentBM25Index(":memory:")
        sparse.ensure(documents, corpus_fingerprint="seeded")
        expected_scores = HybridMemoryRetriever._bm25_scores(
            query,
            [MemoryDocument(item.chunk_id, item.text) for item in documents],
        )
        expected = [
            chunk_id for chunk_id, score in sorted(
                expected_scores.items(), key=lambda item: (-item[1], item[0]),
            ) if score > 0
        ]
        assert sparse.search(query, top_k=len(documents)) == expected


def test_knowledge_base_empty_query_does_not_touch_vector_query():
    knowledge_base = bare_knowledge_base()
    knowledge_base.add_documents([{"id": "kb-one", "title": "x", "content": "事实"}])

    assert knowledge_base.search("   ") == []


def test_bm25_only_strategy_does_not_pay_for_unused_vector_query():
    knowledge_base = bare_knowledge_base()
    knowledge_base._hybrid_retriever = HybridMemoryRetriever(
        vector_weight=0.0, lexical_weight=1.0, recency_weight=0.0,
    )
    knowledge_base.add_documents([
        {"id": "kb-one", "title": "登录", "content": "登录错误 E401 表示令牌过期。"},
    ])
    knowledge_base._collection.query = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("BM25-only must not execute vector query")
    )

    hits = knowledge_base.search("E401", top_k=1)

    assert hits[0]["document_id"] == "kb-one"
    assert hits[0]["sources"] == ["raw:bm25"]


def test_search_uses_persistent_postings_and_never_hydrates_the_full_corpus():
    knowledge_base = bare_knowledge_base()
    knowledge_base.add_documents([
        {"id": "refund", "title": "退款", "content": "退款错误码 R-7。"},
        {"id": "delivery", "title": "配送", "content": "配送需要三天。"},
    ])
    knowledge_base._collection.get_calls.clear()

    hits = knowledge_base.search("R-7", top_k=1)

    assert hits[0]["document_id"] == "refund"
    assert knowledge_base._collection.get_calls
    assert all(call.get("ids") for call in knowledge_base._collection.get_calls)


def test_public_only_scope_filters_sparse_and_dense_before_retrieval():
    knowledge_base = bare_knowledge_base()
    knowledge_base._hybrid_retriever = HybridMemoryRetriever(
        vector_weight=0.0, lexical_weight=1.0, recency_weight=0.0,
    )
    knowledge_base.add_documents([
        {"id": "public", "title": "公开政策", "content": "公开退款说明。"},
    ])
    knowledge_base._collection.add(
        ids=["internal::chunk-0"], documents=["SECRET-INTERNAL"],
        metadatas=[{
            "chunk_id": "internal::chunk-0", "document_id": "internal",
            "title": "内部手册", "scope": "internal",
        }],
    )

    assert knowledge_base.search("SECRET-INTERNAL", top_k=5) == []


def test_sparse_sync_failure_never_serves_a_half_updated_hybrid_index():
    knowledge_base = bare_knowledge_base()

    class BrokenSparse:
        path = ":broken:"
        manifest = {"corpus_fingerprint": "old"}

        def ensure(self, *_args, **_kwargs):
            raise OSError("disk unavailable")

        def search(self, *_args, **_kwargs):
            raise AssertionError("stale sparse index must not be searched")

    knowledge_base._sparse_index = BrokenSparse()
    with pytest.raises(OSError, match="disk unavailable"):
        knowledge_base.add_documents([
            {"id": "new-policy", "title": "新政策", "content": "新的退款期限。"},
        ])

    assert knowledge_base._collection.count() == 1
    assert knowledge_base._sparse_ready is False
    with pytest.raises(RuntimeError, match="not synchronized"):
        knowledge_base.search("退款", top_k=1)


def test_knowledge_base_exposes_retrieval_strategy_with_storage_identity(monkeypatch):
    """部署诊断必须记录实际检索权重，避免离线配置与生产配置漂移。"""
    backend = type("Backend", (), {
        "mode": "embedded",
        "location": "/tmp/test",
        "to_dict": lambda self: {"mode": self.mode, "location": self.location},
    })()
    client = type("Client", (), {
        "get_or_create_collection": lambda self, **_kwargs: FakeCollection(),
    })()
    monkeypatch.setattr("mcp.knowledge_base.create_chroma_client", lambda **_kwargs: (client, backend))

    knowledge_base = KnowledgeBase(
        load_default_docs=False,
        retrieval_rrf_k=42,
        retrieval_vector_weight=0.2,
        retrieval_lexical_weight=0.8,
    )

    assert knowledge_base.storage_backend["retrieval_rrf_k"] == "42"
    assert knowledge_base.storage_backend["retrieval_vector_weight"] == "0.2"
    assert knowledge_base.storage_backend["retrieval_lexical_weight"] == "0.8"


def test_non_empty_incompatible_index_fails_closed(monkeypatch):
    collection = FakeCollection()
    collection.add(
        ids=["old::chunk-0"], documents=["旧索引"],
        metadatas=[{
            "chunking_version": 3,
            "chunk_max_tokens": 360,
            "chunk_overlap_tokens": 48,
            "chunk_strategy": "structure_aware",
        }],
    )
    backend = type("Backend", (), {
        "mode": "embedded", "location": "/tmp/test",
        "to_dict": lambda self: {"mode": self.mode, "location": self.location},
    })()
    client = type("Client", (), {
        "get_or_create_collection": lambda self, **_kwargs: collection,
    })()
    monkeypatch.setattr("mcp.knowledge_base.create_chroma_client", lambda **_kwargs: (client, backend))

    with pytest.raises(IncompatibleKnowledgeIndexError, match="re-import"):
        KnowledgeBase(load_default_docs=False)


def test_old_v4_chunks_without_source_contract_fail_closed(monkeypatch, tmp_path):
    collection = FakeCollection()
    collection.metadata = {
        "index_schema_version": "2",
        "source_contract_version": "1",
        "knowledge_scope": "public",
        "dense_embedding_model": "sentence-transformers/all-MiniLM-L6-v2",
        "sparse_schema_version": "1",
        "sparse_tokenizer_version": "ascii-cjk-unigram-bigram-v1",
        "chunking_version": "4",
        "chunk_max_tokens": "512",
        "chunk_overlap_tokens": "64",
        "chunk_strategy": "fixed_tokens",
    }
    collection.add(
        ids=["old::chunk-0"], documents=["旧 v4 索引"],
        metadatas=[{
            "chunking_version": 4,
            "chunk_max_tokens": 512,
            "chunk_overlap_tokens": 64,
            "chunk_strategy": "fixed_tokens",
        }],
    )
    backend = type("Backend", (), {
        "mode": "embedded", "location": str(tmp_path),
        "to_dict": lambda self: {"mode": self.mode, "location": self.location},
    })()
    client = type("Client", (), {
        "get_or_create_collection": lambda self, **_kwargs: collection,
    })()
    monkeypatch.setattr("mcp.knowledge_base.create_chroma_client", lambda **_kwargs: (client, backend))

    with pytest.raises(IncompatibleKnowledgeIndexError, match="source_contract_version"):
        KnowledgeBase(
            load_default_docs=False,
            sparse_index_path=str(tmp_path / "sparse.db"),
        )


def test_chunker_uses_token_ceiling_structure_boundaries_and_overlap():
    knowledge_base = bare_knowledge_base(max_tokens=24, overlap_tokens=5)
    knowledge_base._chunk_strategy = ChunkStrategy.STRUCTURE_AWARE
    text = (
        "第一段介绍退款审核规则。" * 8
        + "\n第二段包含边界证据 BOUNDARY-42，必须能从相邻片段找回。" * 6
    )

    chunks = knowledge_base._chunk_text(text)

    assert len(chunks) > 2
    assert all(knowledge_base._token_estimator.estimate(chunk) <= 24 for chunk in chunks)
    assert any(chunk.endswith(("。", "\n")) for chunk in chunks[:-1])
    for previous, current in zip(chunks, chunks[1:]):
        overlap_start = knowledge_base._overlap_start(previous, 0, len(previous), 5)
        expected_overlap = previous[overlap_start:]
        assert expected_overlap
        assert current.startswith(expected_overlap)
        assert knowledge_base._token_estimator.estimate(expected_overlap) <= 5


def test_single_oversized_sentence_is_hard_split_without_budget_violation():
    knowledge_base = bare_knowledge_base(max_tokens=32, overlap_tokens=4)
    text = "没有任何句号的超长连续事实" * 100

    chunks = knowledge_base._chunk_text(text)

    assert len(chunks) > 1
    assert all(knowledge_base._token_estimator.estimate(chunk) <= 32 for chunk in chunks)
    assert "".join(chunks).startswith(text[: len(chunks[0])])


def test_multi_chunk_projection_keeps_content_metadata_and_rank_identity_aligned():
    knowledge_base = bare_knowledge_base(max_tokens=40, overlap_tokens=0)
    content = "第一片只有普通背景。" * 20 + "第二片唯一证据 TARGET-Z9。" * 8
    knowledge_base.add_documents([
        {"id": "persisted-document-777", "title": "多片文档", "content": content},
        {"id": "noise-document", "title": "噪声", "content": "无关配送说明。" * 10},
    ])

    hit = knowledge_base.search("TARGET-Z9", top_k=2)[0]
    stored = {
        meta["chunk_id"]: (document, meta)
        for document, meta in zip(
            knowledge_base._collection.documents,
            knowledge_base._collection.metadatas,
        )
    }
    stored_content, stored_meta = stored[hit["chunk_id"]]

    assert hit["document_id"] == "persisted-document-777"
    assert "TARGET-Z9" in hit["content"]
    assert hit["content"] == stored_content
    assert hit["chunk"] == stored_meta["chunk_index"]
    assert hit["chunk_id"] == stored_meta["chunk_id"]


def test_chunk_candidates_keep_stable_ids_until_context_packing():
    knowledge_base = bare_knowledge_base(max_tokens=32, overlap_tokens=4)
    knowledge_base.add_documents([
        {"id": "parent-a", "title": "A", "content": "关键词 ALPHA。" * 100},
        {"id": "parent-b", "title": "B", "content": "关键词 ALPHA 与其他证据。" * 8},
    ])

    hits = knowledge_base.search("ALPHA", top_k=5)

    assert len(hits) == 5
    assert len({hit["chunk_id"] for hit in hits}) == 5
    assert hits[0]["document_id"] == "parent-a"


def test_chunker_budget_and_overlap_reconstruct_seeded_documents():
    """生成多种中英文/结构边界文档，证明切片有界、无缺口且可按 overlap 重组。"""
    rng = random.Random(20260830)
    alphabet = "甲乙丙丁戊己庚辛壬癸ABCDEFGHIJKLMN0123456789"
    separators = "。！？\n"
    for _ in range(300):
        maximum = rng.randint(32, 120)
        overlap = rng.randint(0, maximum // 4)
        knowledge_base = bare_knowledge_base(max_tokens=maximum, overlap_tokens=overlap)
        text = "".join(
            rng.choice(separators) if rng.random() < 0.06 else rng.choice(alphabet)
            for _ in range(rng.randint(300, 1200))
        ).strip()
        spans = knowledge_base._chunk_spans(text)
        chunks = [span.content for span in spans]

        assert chunks
        assert all(knowledge_base._token_estimator.estimate(chunk) <= maximum for chunk in chunks)
        rebuilt = spans[0].content
        previous_end = spans[0].end_char
        for span in spans[1:]:
            assert span.start_char <= previous_end
            rebuilt += span.content[previous_end - span.start_char:]
            previous_end = span.end_char
        assert rebuilt == text


@pytest.mark.parametrize(
    "maximum,overlap",
    [(0, 0), (32, -1), (32, 32), (32, 40)],
)
def test_chunker_rejects_invalid_token_budget_algebra(maximum, overlap):
    knowledge_base = bare_knowledge_base(max_tokens=64, overlap_tokens=8)

    with pytest.raises(ValueError, match="chunk token budgets"):
        knowledge_base._chunk_text(
            "需要切分的事实" * 100,
            max_tokens=maximum,
            overlap_tokens=overlap,
        )
