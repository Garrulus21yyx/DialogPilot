"""混合长期记忆的召回、融合、存储和评测不变量。"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from memory.conversation_memory import MemoryManager, Message, MsgRole
from memory.hybrid_retrieval import (
    HybridMemoryRetriever,
    MemoryDocument,
    evaluate_retrieval,
)


def document(memory_id, content, *, days_ago=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    return MemoryDocument(memory_id, content, timestamp, "conversation")


def test_bm25_recovers_exact_identifier_missed_by_first_vector_result():
    """证明订单号等精确实体可用 BM25 修正纯向量排序。"""
    retriever = HybridMemoryRetriever()
    general = document("general", "用户咨询过订单配送和会员权益", days_ago=0)
    exact = document("exact", "订单 A123 曾因重复扣款提交退款申请", days_ago=10)

    hits = retriever.rank(
        "订单 A123 重复扣款",
        vector_documents=[general, exact],
        corpus_documents=[general, exact],
        top_k=2,
    )

    assert hits[0].memory_id == "exact"
    assert "bm25" in hits[0].sources
    assert hits[0].ranks["bm25"] == 1


def test_recency_only_reranks_relevant_candidate_union():
    """证明最新但无关的记忆不能仅靠时间信号进入结果。"""
    retriever = HybridMemoryRetriever()
    relevant = document("relevant", "登录错误码 E401 与设备验证失败", days_ago=30)
    irrelevant = document("irrelevant", "今天用户询问了会员积分", days_ago=0)

    hits = retriever.rank(
        "E401 登录失败",
        vector_documents=[relevant],
        corpus_documents=[relevant, irrelevant],
        top_k=5,
    )

    assert [hit.memory_id for hit in hits] == ["relevant"]


def test_retrieval_metrics_are_deterministic():
    """证明 Recall@K、MRR 和 nDCG 能作为长期记忆回归门禁。"""
    metrics = evaluate_retrieval(["noise", "wanted", "other"], {"wanted", "missing"}, k=3)

    assert metrics.recall_at_k == 0.5
    assert metrics.reciprocal_rank == 0.5
    assert 0 < metrics.ndcg_at_k < 1


def test_episodic_store_persists_raw_chunks_not_summary():
    """证明摘要只进 metadata，Chroma document 保存可还原的原始片段。"""
    manager = MemoryManager.__new__(MemoryManager)
    manager._episodic = RecordingCollection()
    messages = [
        Message(MsgRole.USER, "订单 A123 重复扣款", message_id="m1"),
        Message(MsgRole.ASSISTANT, "已记录原始事实", message_id="m2"),
    ]
    summary = '{"user_goal":"处理扣款"}'

    assert asyncio.run(manager._archive_messages(
        "user-1", "conv-1", messages, summary=summary, reason="test",
    )) is True

    call = manager._episodic.upsert_call
    assert call["documents"] == ["user: 订单 A123 重复扣款", "assistant: 已记录原始事实"]
    assert call["documents"] != [summary]
    assert call["metadatas"][0]["summary"] == summary
    assert call["metadatas"][0]["memory_version"] == 4
    assert call["metadatas"][0]["message_id"] == "m1"


def test_episodic_archive_is_idempotent_for_the_same_messages():
    """证明压缩 CAS 冲突后的重试会 upsert 同一组 ID，不产生重复记忆。"""
    manager = MemoryManager.__new__(MemoryManager)
    manager._episodic = RecordingCollection()
    messages = [Message(MsgRole.USER, "订单 A123", message_id="stable-message")]

    asyncio.run(manager._archive_messages("u", "c", messages, summary="", reason="compression"))
    first_ids = manager._episodic.upsert_calls[-1]["ids"]
    asyncio.run(manager._archive_messages("u", "c", messages, summary="", reason="compression"))
    second_ids = manager._episodic.upsert_calls[-1]["ids"]

    assert first_ids == second_ids


def test_fact_identity_is_stable_for_the_same_source_operation():
    """证明同一来源事实重试得到同一 ID，而不同来源保留独立历史。"""
    first = MemoryManager._fact_id("user-1", "preferred_language", "zh", ["m1"])
    retry = MemoryManager._fact_id("user-1", "preferred_language", "zh", ["m1"])
    later = MemoryManager._fact_id("user-1", "preferred_language", "zh", ["m2"])

    assert first == retry
    assert first != later


def test_memory_manager_fuses_user_scoped_chroma_candidates():
    """证明存储适配器同时读取向量池和用户内 BM25 语料并返回解释证据。"""
    manager = MemoryManager.__new__(MemoryManager)
    manager._episodic = SearchCollection()
    manager._hybrid_retriever = HybridMemoryRetriever()

    hits = asyncio.run(manager.search_long_term("user-1", "订单 A123", top_k=2))

    assert hits[0].memory_id == "exact"
    assert hits[0].content.startswith("订单 A123")
    assert "bm25" in hits[0].sources
    assert hits[0].message_id == "m-exact"
    assert hits[0].event_seq == 8
    assert hits[0].role == "user"
    assert manager._episodic.where_values == [{"user_id": "user-1"}, {"user_id": "user-1"}]


def test_cross_conversation_search_excludes_current_conversation():
    """立即归档后，当前会话片段不能挤占真正的跨会话召回结果。"""
    manager = MemoryManager.__new__(MemoryManager)
    manager._episodic = SearchCollection()
    manager._hybrid_retriever = HybridMemoryRetriever()

    hits = asyncio.run(manager.search_long_term(
        "user-1",
        "订单 A123",
        top_k=2,
        exclude_conversation_id="c1",
    ))

    assert [hit.memory_id for hit in hits] == ["exact"]
    assert hits[0].conversation_id == "c2"


def test_corrupt_optional_event_locator_does_not_drop_retrievable_content():
    documents = MemoryManager._memory_documents({
        "ids": ["memory-1"],
        "documents": ["订单 A123 原始片段"],
        "metadatas": [{"event_seq": "broken", "chunk_index": object()}],
    }, nested=False)

    assert len(documents) == 1
    assert documents[0].content == "订单 A123 原始片段"
    assert documents[0].event_seq == 0
    assert documents[0].chunk_index == 0


@pytest.mark.parametrize("query", ["\u200b\ufeff", "\u00a0\u2003\u2028\u3000"])
def test_unicode_format_or_whitespace_only_query_returns_before_storage(query):
    manager = MemoryManager.__new__(MemoryManager)
    manager._episodic = SearchCollection()
    manager._hybrid_retriever = HybridMemoryRetriever()

    hits = asyncio.run(manager.search_long_term("user-1", query, top_k=2))

    assert hits == []
    assert manager._episodic.where_values == []


def test_vector_failure_preserves_bm25_recall():
    """证明向量服务故障不会连带抹掉仍可用的关键词召回。"""
    manager = MemoryManager.__new__(MemoryManager)
    manager._episodic = LexicalOnlyCollection()
    manager._hybrid_retriever = HybridMemoryRetriever()

    hits = asyncio.run(manager.search_long_term("user-1", "E401", top_k=1))

    assert [hit.memory_id for hit in hits] == ["exact"]
    assert hits[0].sources == ("bm25", "recency")


class RecordingCollection:
    def __init__(self):
        self.upsert_call = None
        self.upsert_calls = []

    def upsert(self, **kwargs):
        self.upsert_call = kwargs
        self.upsert_calls.append(kwargs)


class SearchCollection:
    def __init__(self):
        self.where_values = []

    def query(self, **kwargs):
        self.where_values.append(kwargs["where"])
        return {
            "ids": [["general", "exact"]],
            "documents": [["订单配送咨询", "订单 A123 曾重复扣款"]],
            "metadatas": [[
                {"conv_id": "c1", "ts": "2026-08-29T10:00:00+00:00", "message_id": "m-general", "event_seq": 2, "role": "assistant"},
                {"conv_id": "c2", "ts": "2026-08-20T10:00:00+00:00", "message_id": "m-exact", "event_seq": 8, "role": "user"},
            ]],
            "distances": [[0.1, 0.2]],
        }

    def get(self, **kwargs):
        self.where_values.append(kwargs["where"])
        return {
            "ids": ["general", "exact"],
            "documents": ["订单配送咨询", "订单 A123 曾重复扣款"],
            "metadatas": [
                {"conv_id": "c1", "ts": "2026-08-29T10:00:00+00:00", "message_id": "m-general", "event_seq": 2, "role": "assistant"},
                {"conv_id": "c2", "ts": "2026-08-20T10:00:00+00:00", "message_id": "m-exact", "event_seq": 8, "role": "user"},
            ],
        }


class LexicalOnlyCollection:
    def query(self, **_kwargs):
        raise RuntimeError("embedding unavailable")

    def get(self, **_kwargs):
        return {
            "ids": ["exact", "noise"],
            "documents": ["登录失败错误码 E401", "会员积分说明"],
            "metadatas": [{"ts": "2026-08-01T00:00:00+00:00"}, {}],
        }
