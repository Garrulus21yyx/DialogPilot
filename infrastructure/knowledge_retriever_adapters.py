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

    async def rerank(self, query: str, candidates: Sequence[Mapping[str, Any]]):
        projected = candidates_from_items(candidates)
        result = await self.reranker.rerank(query, projected)
        return result.ordered_ids, result.error is not None
