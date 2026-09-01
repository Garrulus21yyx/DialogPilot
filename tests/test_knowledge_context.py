import asyncio
from types import SimpleNamespace

from api import main
from core.intent_recognizer import IntentCategory


class FakeToolManager:
    def __init__(self, items):
        self._items = items

    async def search_with_rewrite(self, *_args, **_kwargs):
        return SimpleNamespace(success=True, data=self._items)


def test_rag_fallback_is_not_published_as_business_evidence(monkeypatch):
    """证明 fallback 诊断文本不会被标记为知识证据或注入回答上下文。"""
    monkeypatch.setattr(main, "_tool_manager", FakeToolManager([{
        "title": "知识库降级结果",
        "content": "知识库暂时不可用",
        "score": 0.0,
        "fallback": True,
    }]))

    context, used = asyncio.run(main._build_knowledge_context(
        "退款政策是什么",
        intent=IntentCategory.REFUND,
    ))

    assert context == ""
    assert used is False


def test_real_rag_result_is_marked_as_used(monkeypatch):
    """证明真实检索结果才会进入知识上下文并标记 knowledge_used。"""
    monkeypatch.setattr(main, "_tool_manager", FakeToolManager([{
        "title": "退款政策",
        "content": "购买后七天内可以申请退款。",
        "score": 0.9,
    }]))

    context, used = asyncio.run(main._build_knowledge_context(
        "退款政策是什么",
        intent=IntentCategory.REFUND,
    ))

    assert "退款政策" in context
    assert used is True


def test_chat_rag_packs_top_five_and_injects_only_validated_grounded_draft(monkeypatch):
    items = [{
        "document_id": f"doc-{index}",
        "chunk_id": f"chunk-{index}",
        "title": f"政策 {index}",
        "content": f"第 {index} 条退款政策证据。",
        "source_start_char": 0,
        "source_end_char": 12,
    } for index in range(6)]
    captured = {}

    class Generator:
        async def generate(self, query, contexts, *, history):
            captured["query"] = query
            captured["contexts"] = contexts
            captured["history"] = history
            return SimpleNamespace(
                answer="退款需要按政策审核。",
                citations=(contexts[0].chunk_id,),
                abstained=False,
                claims=(SimpleNamespace(
                    text="退款需要按政策审核。", citations=(contexts[0].chunk_id,),
                ),),
                conflicts=(),
            )

    monkeypatch.setattr(main, "_tool_manager", FakeToolManager(items))
    monkeypatch.setattr(main, "_grounded_answer_generator", Generator())

    result = asyncio.run(main._build_knowledge_context(
        "它怎么退款？",
        intent=IntentCategory.REFUND,
        history=["用户之前提到订单 A1"],
    ))

    assert result.used is True
    assert result.generation_status == "grounded_draft"
    assert result.citations == ("chunk-0",)
    assert result.claims[0]["citations"] == ["chunk-0"]
    assert result.evidence_pack.items[0].scope_decision == "allowed_public"
    assert len(captured["contexts"]) == 5
    assert "chunk-5" not in result.text
    assert "退款需要按政策审核" in result.text


def test_publication_uses_grounded_answer_only_for_knowledge_only_request():
    knowledge = main.KnowledgeContextResult(
        used=True,
        generation_status="grounded_draft",
        answer="政策要求七天内申请。",
        citations=("c1",),
        claims=({"text": "政策要求七天内申请。", "citations": ["c1"]},),
    )

    candidate, final = main._select_publication_candidate(
        "Agent 改写后的政策答案", knowledge,
        [{"tool_name": "knowledge_search"}], approval_pending=False,
    )
    assert candidate == knowledge.answer
    assert final is True

    mixed_candidate, mixed_final = main._select_publication_candidate(
        "政策允许，订单 A1 当前也符合。", knowledge,
        [{"tool_name": "knowledge_search"}, {"tool_name": "order_lookup"}],
        approval_pending=False,
    )
    assert mixed_candidate == "政策允许，订单 A1 当前也符合。"
    assert mixed_final is False

    abstention = main.KnowledgeContextResult(
        used=True,
        generation_status="abstained",
        answer="知识库证据互相冲突，请人工确认。",
        conflicts=({"description": "退款期限冲突", "citations": ["c1", "c2"]},),
        abstained=True,
        reason="conflicting_evidence",
    )
    abstained_candidate, abstained_final = main._select_publication_candidate(
        "Agent 自行选择了七天。", abstention,
        [{"tool_name": "knowledge_search"}], approval_pending=False,
    )
    assert abstained_candidate == abstention.answer
    assert abstained_final is True


def test_rag_cache_scope_changes_with_index_manifest(monkeypatch):
    fake = SimpleNamespace(index_manifest={"manifest_fingerprint": "corpus-a"})
    monkeypatch.setattr(main, "_knowledge_base", fake)
    first = main._rag_cache_scope("bundle-x")
    fake.index_manifest = {"manifest_fingerprint": "corpus-b"}
    second = main._rag_cache_scope("bundle-x")

    assert first == "bundle-x:corpus-a"
    assert second == "bundle-x:corpus-b"
    assert first != second
"""RAG 真实证据与工具降级信息之间的信任边界测试。"""
