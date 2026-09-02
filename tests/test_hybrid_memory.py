"""混合长期记忆的召回、融合、存储和评测不变量。"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


def test_fact_identity_is_stable_for_the_same_source_operation():
    """证明同一来源事实重试得到同一 ID，而不同来源保留独立历史。"""
    first = MemoryManager._fact_id("user-1", "preferred_language", "zh", ["m1"])
    retry = MemoryManager._fact_id("user-1", "preferred_language", "zh", ["m1"])
    later = MemoryManager._fact_id("user-1", "preferred_language", "zh", ["m2"])

    assert first == retry
    assert first != later


def test_compatibility_context_is_current_thread_only(monkeypatch):
    manager = MemoryManager.__new__(MemoryManager)

    async def checkpoint(*_args):
        return SimpleNamespace(covered_until_seq=3), None

    async def chunks(*_args):
        return []

    async def recent(*_args, **_kwargs):
        return [Message(MsgRole.USER, "当前会话", seq=4)]

    async def profile(*_args):
        return {"language": "zh-CN"}

    monkeypatch.setattr(manager, "_read_checkpoint", checkpoint)
    monkeypatch.setattr(manager, "_get_summary_chunks", chunks)
    monkeypatch.setattr(manager, "_get_working_memory", recent)
    monkeypatch.setattr(manager, "_get_profile", profile)
    monkeypatch.setattr(manager, "_build_summary_view", lambda *_args: "summary")
    context = asyncio.run(manager.get_context(
        "user-1", "conversation-1", query="must not prefetch",
    ))
    assert [item.content for item in context.recent_messages] == ["当前会话"]
    assert context.summary == "summary"
    assert context.user_profile == {"language": "zh-CN"}
    assert context.relevant_history == []
    assert context.retrieval_hits == []
