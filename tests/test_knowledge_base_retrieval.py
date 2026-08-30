"""知识库来源 ID 与混合召回合同。"""
from memory.hybrid_retrieval import HybridMemoryRetriever
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


def bare_knowledge_base():
    knowledge_base = KnowledgeBase.__new__(KnowledgeBase)
    knowledge_base._collection = FakeCollection()
    knowledge_base._hybrid_retriever = HybridMemoryRetriever(recency_weight=0.0)
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
    assert "bm25" in hits[0]["sources"]
    assert "recency" not in hits[0]["sources"]
    assert knowledge_base._collection.metadatas[0]["document_id"] == "kb-target"


def test_knowledge_base_empty_query_does_not_touch_vector_query():
    knowledge_base = bare_knowledge_base()
    knowledge_base.add_documents([{"id": "kb-one", "title": "x", "content": "事实"}])

    assert knowledge_base.search("   ") == []
