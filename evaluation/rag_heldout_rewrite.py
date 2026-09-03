"""Capture one production-owned standalone rewrite per heldout case."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from application.knowledge_retriever import KnowledgeQueryTransformer
from core.llm_metrics import capture_llm_usage
from evaluation.rag_pipeline.contracts import RagCase


@dataclass(frozen=True)
class RewriteCapture:
    standalone: str
    status: str
    error: str | None
    usage: Mapping[str, Any]
    provider_request_ids: tuple[str, ...]


async def capture_rewrites(
    cases: Sequence[RagCase],
    transformer: KnowledgeQueryTransformer,
    concurrency: int,
) -> dict[str, RewriteCapture]:
    """Call only ``standalone`` and preserve production fallback semantics."""
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: RagCase) -> tuple[str, RewriteCapture]:
        async with semaphore:
            with capture_llm_usage() as collector:
                standalone, error = await transformer.standalone(
                    case.query, case.history
                )
            summary = collector.summary()
            if int(summary["total"]["calls"]) > 1:
                raise ValueError("heldout rewrite must call the model at most once")
            return case.case_id, RewriteCapture(
                standalone=str(standalone),
                status=rewrite_status(case.query, standalone, error),
                error=str(error) if error else None,
                usage=dict(summary["total"]),
                provider_request_ids=tuple(
                    str(item["provider_request_id"])
                    for item in summary["calls"]
                    if item.get("provider_request_id")
                ),
            )

    return dict(await asyncio.gather(*(one(case) for case in cases)))


def rewrite_status(raw: str, standalone: str, error: str | None) -> str:
    if error:
        return "FALLBACK_ERROR"
    if not str(standalone).strip():
        return "FALLBACK_EMPTY"
    if str(standalone) == str(raw):
        return "FALLBACK_IDENTICAL"
    return "REWRITTEN"
