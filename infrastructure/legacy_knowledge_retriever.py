"""Legacy Chroma/BM25 adapters behind the KnowledgeRetriever owner."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from application.knowledge_retriever import KnowledgeRetrievalRequest
from mcp.evidence_pack import EvidencePack
from mcp.result_reranker import candidates_from_items


class ToolManagerQueryTransformerAdapter:
    def __init__(self, tool_manager):
        self.transformer = tool_manager._query_transformer

    async def standalone(self, query: str, history: Sequence[str]):
        return await self.transformer.standalone(query, tuple(history))


class ToolManagerRerankerAdapter:
    def __init__(self, tool_manager):
        self.reranker = tool_manager._result_reranker

    async def rerank(self, query: str, candidates: Sequence[Mapping[str, Any]]):
        projected = candidates_from_items(candidates)
        result = await self.reranker.rerank(query, projected)
        return result.ordered_ids, result.error is not None


class LegacyKnowledgeEvidenceValidator:
    """Re-resolve cached refs against the current KnowledgeBase manifest."""

    def __init__(self, knowledge_base):
        self.knowledge_base = knowledge_base

    def validate_candidates(
        self,
        candidates: Sequence[Mapping[str, Any]],
        request: KnowledgeRetrievalRequest,
    ) -> bool:
        if self._manifest() != request.manifest_fingerprint:
            return False
        return self.knowledge_base.validate_cached_candidates(candidates)

    def validate(
        self, pack: EvidencePack, request: KnowledgeRetrievalRequest,
    ) -> bool:
        if (
            self._manifest() != request.manifest_fingerprint
            or pack.index_manifest_fingerprint != request.manifest_fingerprint
        ):
            return False
        candidates = [{
            "chunk_id": item.chunk_id,
            "source_id": item.source_ref.source_id,
            "source_revision": item.source_ref.source_revision,
            "source_checksum": item.source_ref.checksum,
            "source_start_char": item.source_ref.start_char,
            "source_end_char": item.source_ref.end_char,
            "scope": item.source_ref.scope,
            "content": item.text,
        } for item in pack.items]
        return self.knowledge_base.validate_cached_candidates(candidates)

    def _manifest(self) -> str:
        return str(
            self.knowledge_base.index_manifest.get("manifest_fingerprint") or "",
        )
