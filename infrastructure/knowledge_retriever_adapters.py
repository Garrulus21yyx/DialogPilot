"""Adapters from MCP model helpers into the KnowledgeRetriever owner."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from mcp.result_reranker import candidates_from_items


class ToolManagerQueryTransformerAdapter:
    def __init__(self, tool_manager):
        self.transformer = tool_manager._query_transformer

    async def standalone(self, query: str, history: Sequence[str]):
        return await self.transformer.standalone(query, tuple(history))

    async def expand(self, query: str, *, n: int):
        return await self.transformer.expand(query, n=n)


class ToolManagerRerankerAdapter:
    def __init__(self, tool_manager):
        self.reranker = tool_manager._result_reranker

    @property
    def version(self):
        return self.reranker.version

    async def rerank(self, query: str, candidates: Sequence[Mapping[str, Any]]):
        projected = candidates_from_items(candidates)
        result = await self.reranker.rerank(query, projected)
        return result.ordered_ids, result.error is not None


def configured_knowledge_reranker(tool_manager, values):
    mode = values.get("RAG_RERANKER", "listwise")
    if mode == "listwise":
        return ToolManagerRerankerAdapter(tool_manager)
    if mode == "local_bge":
        from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
        return LocalKnowledgeReranker(values["RAG_LOCAL_RERANKER_PATH"],
            device=values.get("RAG_LOCAL_RERANKER_DEVICE", "cpu"),
            batch_size=int(values.get("RAG_LOCAL_RERANKER_BATCH_SIZE", "4")))
    raise ValueError("unsupported RAG reranker")
