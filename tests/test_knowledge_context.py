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
