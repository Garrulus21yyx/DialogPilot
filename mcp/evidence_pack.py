"""从检索到最终生成边界保持不丢失的客服知识证据包。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from mcp.context_packer import PackedContext


@dataclass(frozen=True)
class SourceReference:
    source_id: str
    source_revision: str
    start_char: int
    end_char: int
    source_type: str
    checksum: str
    scope: str = "public"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_revision": self.source_revision,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "source_type": self.source_type,
            "checksum": self.checksum,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class EvidenceItem:
    chunk_id: str
    title: str
    source_ref: SourceReference
    score: float
    rank: int
    source_ranks: tuple[tuple[str, int], ...]
    scope_decision: str
    text: str

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        value = {
            "chunk_id": self.chunk_id,
            "title": self.title,
            "source_ref": self.source_ref.to_dict(),
            "score": self.score,
            "rank": self.rank,
            "source_ranks": dict(self.source_ranks),
            "scope_decision": self.scope_decision,
        }
        if include_text:
            value["text"] = self.text
        return value


@dataclass(frozen=True)
class EvidencePack:
    query: str
    index_manifest_fingerprint: str
    retrieval_policy: tuple[tuple[str, Any], ...]
    items: tuple[EvidenceItem, ...]
    query_variants: tuple[tuple[str, str, float], ...] = ()
    rewrite_prompt_version: str = ""
    rewrite_error: str = ""
    rerank_prompt_version: str = ""
    rerank_error: str = ""
    skipped_redundant: tuple[str, ...] = ()
    skipped_budget: tuple[str, ...] = ()

    @classmethod
    def from_packed(
        cls,
        query: str,
        packed: PackedContext,
        *,
        retrieval_policy: Mapping[str, Any],
        retrieval_trace: Mapping[str, Any] | None = None,
    ) -> "EvidencePack":
        items = tuple(
            EvidenceItem(
                chunk_id=item.chunk_id,
                title=item.title,
                source_ref=SourceReference(
                    source_id=item.document_id,
                    source_revision=item.source_revision,
                    start_char=item.start_char,
                    end_char=item.end_char,
                    source_type=item.source_type,
                    checksum=item.source_checksum,
                    scope=item.scope,
                ),
                score=item.score,
                rank=index,
                source_ranks=item.ranks,
                scope_decision=item.scope_decision,
                text=item.text,
            )
            for index, item in enumerate(packed.selected, 1)
        )
        fingerprints = {
            item.index_manifest_fingerprint for item in packed.selected
            if item.index_manifest_fingerprint
        }
        if len(fingerprints) > 1:
            raise ValueError("packed evidence spans multiple index manifests")
        fingerprint = next(iter(fingerprints), "")
        trace = dict(retrieval_trace or {})
        variants = tuple(
            (str(value.get("kind") or ""), str(value.get("query") or ""), float(value.get("weight") or 0))
            for value in (trace.get("variants") or []) if isinstance(value, Mapping)
        )
        return cls(
            query=str(query),
            index_manifest_fingerprint=fingerprint,
            retrieval_policy=tuple(sorted(
                (str(key), value) for key, value in retrieval_policy.items()
                if isinstance(value, (str, int, float, bool))
            )),
            items=items,
            query_variants=variants,
            rewrite_prompt_version=str(trace.get("rewrite_prompt_version") or ""),
            rewrite_error=str(trace.get("rewrite_error") or ""),
            rerank_prompt_version=str(trace.get("rerank_prompt_version") or ""),
            rerank_error=str(trace.get("rerank_error") or ""),
            skipped_redundant=packed.skipped_redundant,
            skipped_budget=packed.skipped_budget,
        )

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        return {
            "query": self.query,
            "index_manifest_fingerprint": self.index_manifest_fingerprint,
            "retrieval_policy": dict(self.retrieval_policy),
            "items": [item.to_dict(include_text=include_text) for item in self.items],
            "query_variants": [
                {"kind": kind, "query": query, "weight": weight}
                for kind, query, weight in self.query_variants
            ],
            "rewrite_prompt_version": self.rewrite_prompt_version,
            "rewrite_error": self.rewrite_error,
            "rerank_prompt_version": self.rerank_prompt_version,
            "rerank_error": self.rerank_error,
            "skipped_redundant": list(self.skipped_redundant),
            "skipped_budget": list(self.skipped_budget),
        }
