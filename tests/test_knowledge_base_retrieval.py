"""知识库来源 ID、Token overlap 切片与混合召回合同。"""
import random

import pytest

from memory.hybrid_retrieval import HybridMemoryRetriever
from memory.context import TokenEstimator
from mcp.knowledge_base import KnowledgeBase


class FakeCollection:
    def __init__(self):
        self.ids = []
        self.documents = []
        self.metadatas = []

    def count(self):
        return len(self.ids)

    def add(self, *, ids, documents, metadatas):
        self.ids.extend(ids)
        self.documents.extend(documents)
        self.metadatas.extend(metadatas)

    def query(self, **_kwargs):
        order = list(reversed(range(len(self.ids))))
        return {
            "ids": [[self.ids[index] for index in order]],
            "documents": [[self.documents[index] for index in order]],
            "metadatas": [[self.metadatas[index] for index in order]],
            "distances": [[float(index) for index in order]],
        }

    def get(self, **_kwargs):
        return {
            "ids": list(self.ids),
            "documents": list(self.documents),
            "metadatas": list(self.metadatas),
        }


def bare_knowledge_base(*, max_tokens=360, overlap_tokens=48):
    knowledge_base = KnowledgeBase.__new__(KnowledgeBase)
    knowledge_base._collection = FakeCollection()
    knowledge_base._hybrid_retriever = HybridMemoryRetriever(recency_weight=0.0)
    knowledge_base._token_estimator = TokenEstimator()
    knowledge_base._chunk_max_tokens = max_tokens
    knowledge_base._chunk_overlap_tokens = overlap_tokens
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
    assert "bm25" in hits[0]["sources"]
    assert "recency" not in hits[0]["sources"]
    assert knowledge_base._collection.metadatas[0]["document_id"] == "kb-target"
    assert knowledge_base._collection.metadatas[0]["chunking_version"] == 2


def test_knowledge_base_empty_query_does_not_touch_vector_query():
    knowledge_base = bare_knowledge_base()
    knowledge_base.add_documents([{"id": "kb-one", "title": "x", "content": "事实"}])

    assert knowledge_base.search("   ") == []


def test_chunker_uses_token_ceiling_structure_boundaries_and_overlap():
    knowledge_base = bare_knowledge_base(max_tokens=24, overlap_tokens=5)
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


def test_parent_document_is_deduplicated_only_after_chunk_ranking():
    knowledge_base = bare_knowledge_base(max_tokens=32, overlap_tokens=4)
    knowledge_base.add_documents([
        {"id": "parent-a", "title": "A", "content": "关键词 ALPHA。" * 100},
        {"id": "parent-b", "title": "B", "content": "关键词 ALPHA 与其他证据。" * 8},
    ])

    hits = knowledge_base.search("ALPHA", top_k=5)

    assert [hit["document_id"] for hit in hits] == ["parent-a", "parent-b"]
    assert len({hit["chunk_id"] for hit in hits}) == 2


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
        chunks = knowledge_base._chunk_text(text)

        assert chunks
        assert all(knowledge_base._token_estimator.estimate(chunk) <= maximum for chunk in chunks)
        rebuilt = chunks[0]
        for chunk in chunks[1:]:
            shared = max(
                (
                    size
                    for size in range(min(len(rebuilt), len(chunk)), -1, -1)
                    if rebuilt.endswith(chunk[:size])
                ),
                default=0,
            )
            rebuilt += chunk[shared:]
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
