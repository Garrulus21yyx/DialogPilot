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
    assert len(captured["contexts"]) == 5
    assert "chunk-5" not in result.text
    assert "退款需要按政策审核" in result.text
"""RAG 真实证据与工具降级信息之间的信任边界测试。"""
