import asyncio
from types import SimpleNamespace

from api import main
from application.hybrid_retrieval import RetrievalStatus
from application.knowledge_retriever import EvidencePackResult
from core.intent_recognizer import IntentCategory
from mcp.context_packer import ContextCandidate, PackedContext
from mcp.evidence_pack import EvidencePack
from core.auth import Principal


class FakeRetriever:
    def __init__(self, items):
        self._items = items

    async def retrieve(self, request):
        if not self._items:
            return EvidencePackResult(
                RetrievalStatus.UNAVAILABLE, None, None, "TEST_UNAVAILABLE",
            )
        candidates = tuple(ContextCandidate(
            chunk_id=str(item["chunk_id"]), document_id=str(item["source_id"]),
            text=str(item["content"]), start_char=0,
            end_char=len(str(item["content"])), title=str(item["title"]),
            source_type="text", source_checksum="a" * 64,
            source_revision="revision-one", scope="public",
            scope_decision="allowed_public",
            index_manifest_fingerprint=request.manifest_fingerprint,
        ) for item in self._items[:5])
        packed = PackedContext(
            tuple(item.chunk_id for item in candidates),
            sum(len(item.text) for item in candidates), selected=candidates,
        )
        pack = EvidencePack.from_packed(
            request.query, packed,
            retrieval_policy=request.policy.legacy_mapping(),
        )
        return EvidencePackResult(RetrievalStatus.OK, pack, None)


def _identity_kwargs():
    return {
        "tenant_id": "tenant-test", "user_id": "user-test",
        "conversation_id": "", "authorization_fingerprint": "auth-test",
    }


def test_rag_fallback_is_not_published_as_business_evidence(monkeypatch):
    """证明 fallback 诊断文本不会被标记为知识证据或注入回答上下文。"""
    monkeypatch.setattr(main, "_knowledge_retriever", FakeRetriever([]))
    monkeypatch.setattr(main, "_knowledge_base", SimpleNamespace(
        index_manifest={"manifest_fingerprint": "a" * 64},
        DENSE_EMBEDDING_MODEL="test", DENSE_EMBEDDING_FUNCTION="test",
    ))

    context, used = asyncio.run(main._build_knowledge_context(
        "退款政策是什么",
        intent=IntentCategory.REFUND,
        **_identity_kwargs(),
    ))

    assert context == ""
    assert used is False


def test_real_rag_result_is_marked_as_used(monkeypatch):
    """证明真实检索结果才会进入知识上下文并标记 knowledge_used。"""
    monkeypatch.setattr(main, "_knowledge_retriever", FakeRetriever([{
        "chunk_id": "chunk-one", "source_id": "refund-policy",
        "title": "退款政策",
        "content": "购买后七天内可以申请退款。",
    }]))
    monkeypatch.setattr(main, "_knowledge_base", SimpleNamespace(
        index_manifest={"manifest_fingerprint": "a" * 64},
        DENSE_EMBEDDING_MODEL="test", DENSE_EMBEDDING_FUNCTION="test",
    ))

    context, used = asyncio.run(main._build_knowledge_context(
        "退款政策是什么",
        intent=IntentCategory.REFUND,
        **_identity_kwargs(),
    ))

    assert "退款政策" in context
    assert used is True


def test_chat_rag_packs_top_five_and_injects_only_validated_grounded_draft(monkeypatch):
    items = [{
        "chunk_id": f"chunk-{index}",
        "source_id": f"doc-{index}",
        "title": f"政策 {index}",
        "content": f"第 {index} 条退款政策证据。",
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

    monkeypatch.setattr(main, "_knowledge_retriever", FakeRetriever(items))
    monkeypatch.setattr(main, "_knowledge_base", SimpleNamespace(
        index_manifest={"manifest_fingerprint": "a" * 64},
        DENSE_EMBEDDING_MODEL="test", DENSE_EMBEDDING_FUNCTION="test",
    ))
    monkeypatch.setattr(main, "_grounded_answer_generator", Generator())

    result = asyncio.run(main._build_knowledge_context(
        "它怎么退款？",
        intent=IntentCategory.REFUND,
        history=["用户之前提到订单 A1"],
        **_identity_kwargs(),
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
    captured = []

    class CapturingRetriever:
        async def retrieve(self, request):
            captured.append(request)
            return EvidencePackResult(RetrievalStatus.NO_EVIDENCE, None, None)

    fake = SimpleNamespace(
        index_manifest={"manifest_fingerprint": "a" * 64},
        DENSE_EMBEDDING_MODEL="test", DENSE_EMBEDDING_FUNCTION="test",
    )
    monkeypatch.setattr(main, "_knowledge_base", fake)
    monkeypatch.setattr(main, "_knowledge_retriever", CapturingRetriever())
    asyncio.run(main._retrieve_knowledge(
        "退款", history=(), policy_values={}, policy_version="bundle-x",
        tenant_id="tenant", user_scope="user", conversation_id="",
        authorization_fingerprint="auth", requirement_signature="knowledge",
    ))
    fake.index_manifest = {"manifest_fingerprint": "b" * 64}
    asyncio.run(main._retrieve_knowledge(
        "退款", history=(), policy_values={}, policy_version="bundle-x",
        tenant_id="tenant", user_scope="user", conversation_id="",
        authorization_fingerprint="auth", requirement_signature="knowledge",
    ))
    assert captured[0].manifest_fingerprint == "a" * 64
    assert captured[1].manifest_fingerprint == "b" * 64
    assert captured[0].generation_id != captured[1].generation_id


def test_all_knowledge_consumers_share_one_evidence_pack_and_identity(monkeypatch):
    retriever = FakeRetriever([{
        "chunk_id": "chunk-one", "source_id": "refund-policy",
        "title": "退款政策", "content": "七天内可申请退款。",
    }])

    class Bundle:
        retrieval_policy = {}

        @staticmethod
        def component_hash(_component):
            return "bundle-policy-v1"

    bundle = Bundle()
    monkeypatch.setattr(main, "_knowledge_retriever", retriever)
    monkeypatch.setattr(main, "_knowledge_base", SimpleNamespace(
        index_manifest={"manifest_fingerprint": "a" * 64},
        DENSE_EMBEDDING_MODEL="test", DENSE_EMBEDDING_FUNCTION="test",
    ))
    monkeypatch.setattr(main, "_bundle_registry", SimpleNamespace(active=lambda: bundle))
    monkeypatch.setattr(main, "_grounded_answer_generator", None)
    principal = Principal(subject="user-test", scopes=frozenset({"knowledge:read"}))
    authorization = main._fingerprint({
        "subject": principal.subject, "scopes": sorted(principal.scopes),
    })
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-test")

    direct = asyncio.run(main._retrieve_knowledge(
        "退款政策是什么", history=(), policy_values={},
        policy_version="bundle-policy-v1", tenant_id="tenant-test",
        user_scope="user-test", conversation_id="",
        authorization_fingerprint=authorization,
        requirement_signature="knowledge.active_source",
    ))
    api_result = asyncio.run(main.search("退款政策是什么", 5, principal))
    tool_result = asyncio.run(main._knowledge_tool_handler(
        {"query": "退款政策是什么"},
        {
            "tenant_id": "tenant-test", "user_id": "user-test", "conv_id": "",
            "authorization_fingerprint": authorization,
            "retrieval_policy": {}, "cache_scope": "bundle-policy-v1",
        },
    ))
    pre_knowledge = asyncio.run(main._build_knowledge_context(
        "退款政策是什么", intent=IntentCategory.REFUND,
        bundle=bundle, tenant_id="tenant-test", user_id="user-test",
        conversation_id="", authorization_fingerprint=authorization,
    ))

    packs = (
        direct.evidence_pack.to_dict(include_text=True),
        api_result["evidence_pack"], tool_result["evidence_pack"],
        pre_knowledge.evidence_pack.to_dict(include_text=True),
    )
    assert all(pack == packs[0] for pack in packs)
    assert packs[0]["items"][0]["chunk_id"] == "chunk-one"


def test_unavailable_status_is_identical_for_api_agent_and_pre_knowledge(monkeypatch):
    monkeypatch.setattr(main, "_knowledge_retriever", FakeRetriever([]))
    monkeypatch.setattr(main, "_knowledge_base", SimpleNamespace(
        index_manifest={"manifest_fingerprint": "a" * 64},
        DENSE_EMBEDDING_MODEL="test", DENSE_EMBEDDING_FUNCTION="test",
    ))
    monkeypatch.setattr(main, "_bundle_registry", None)
    principal = Principal(subject="user-test", scopes=frozenset({"knowledge:read"}))
    authorization = main._fingerprint({
        "subject": principal.subject, "scopes": sorted(principal.scopes),
    })
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tenant-test")

    api_result = asyncio.run(main.search("退款", 5, principal))
    tool_result = asyncio.run(main._knowledge_tool_handler(
        {"query": "退款"}, {
            "tenant_id": "tenant-test", "user_id": "user-test", "conv_id": "",
            "authorization_fingerprint": authorization,
            "retrieval_policy": {}, "cache_scope": "default",
        },
    ))
    pre_knowledge = asyncio.run(main._build_knowledge_context(
        "退款", intent=IntentCategory.REFUND,
        tenant_id="tenant-test", user_id="user-test", conversation_id="",
        authorization_fingerprint=authorization,
    ))

    assert api_result["status"] == tool_result["status"] == "UNAVAILABLE"
    assert api_result["evidence_pack"] is tool_result["evidence_pack"] is None
    assert pre_knowledge.used is False
    assert pre_knowledge.generation_status == "unavailable"
"""RAG 真实证据与工具降级信息之间的信任边界测试。"""
