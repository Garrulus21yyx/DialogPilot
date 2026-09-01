"""Compare fixed and mature hierarchical retrieval/packing contracts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from anthropic import AsyncAnthropic

from core.llm_metrics import capture_llm_usage
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_generation_evaluation import JUDGE_PROMPT_VERSION, _judge
from evaluation.rag_hierarchical_adapter import (
    HierarchyIndex,
    budget_aware_mixed_candidates,
    build_hierarchy,
    dynamic_auto_merge_candidates,
    hierarchy_contract,
    transform_leaf_hit,
    unique_parent_candidates,
)
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.fusion import fuse_rankings
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits, project_chunks
from evaluation.rag_query_ablation import _variants, _weights
from memory.context import TokenEstimator
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.document_chunker import DocumentChunker
from mcp.grounded_answer_generator import (
    GROUNDED_GENERATION_PROMPT_VERSION,
    GroundedAnswerGenerator,
)
from mcp.knowledge_base import KnowledgeBase
from mcp.result_reranker import RERANK_PROMPT_VERSION, RerankCandidate, ResultReranker


TOPOLOGIES = (
    "baseline-512",
    "neighbor-512",
    "parent-child-256-1024",
    "conditional-parent-child",
    "unique-parent-aggregation",
    "dynamic-auto-merge",
    "budget-aware-mixed",
)
DEFAULT_TOPOLOGIES = (
    "baseline-512",
    "parent-child-256-1024",
    "unique-parent-aggregation",
    "dynamic-auto-merge",
    "budget-aware-mixed",
)
HIERARCHICAL_TOPOLOGIES = frozenset({
    "unique-parent-aggregation",
    "dynamic-auto-merge",
    "budget-aware-mixed",
})
CONTEXT_MAX_TOKENS = 2600
FINAL_K = 5
CANDIDATE_K = 20
LONG_DOCUMENT_CHARS = 8000


@dataclass(frozen=True)
class ParentRecord:
    parent_id: str
    source_id: str
    title: str
    text: str
    start_char: int
    end_char: int


def _query_contract(query_capture: Mapping[str, Any]) -> str:
    has_standalone = any(
        str(row.get("standalone") or "").strip()
        and str(row.get("standalone") or "").strip() != str(row.get("raw_query") or "").strip()
        for row in query_capture.get("rows") or ()
    )
    return (
        "Raw .25 + captured Standalone .75; BM25 .75 + Dense .25; RRF k=10"
        if has_standalone else
        "Raw-only 1.0; BM25 .75 + Dense .25; RRF k=10"
    )


def _grounding_limitation(dataset: RagDataset) -> str:
    granularity = str(dataset.manifest.get("source", {}).get("grounding_granularity") or "")
    if granularity.startswith("article-level"):
        return (
            "WixQA Gold identifies relevant articles, not answer spans; document Recall and "
            "multi-article packing are valid, but section localization is not measured."
        )
    return "Doc2Dial Gold spans are authoritative but not exhaustive relevance annotations."


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(map(float, values))
    position = (len(ordered) - 1) * percentile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def _covers(candidate: ContextCandidate | Mapping[str, Any], evidence: EvidenceSpan) -> bool:
    if isinstance(candidate, ContextCandidate):
        document_id, start, end = candidate.document_id, candidate.start_char, candidate.end_char
    else:
        document_id = str(candidate.get("document_id") or "")
        start = int(candidate.get("source_start_char") or 0)
        end = int(candidate.get("source_end_char") or 0)
    if evidence.granularity == "document":
        return document_id == evidence.document_id
    return document_id == evidence.document_id and start <= evidence.start_char and end >= evidence.end_char


def _context_recall(case: RagCase, contexts: Sequence[ContextCandidate]) -> float:
    if not case.evidence:
        return 1.0
    covered = sum(any(_covers(context, evidence) for context in contexts) for evidence in case.evidence)
    return covered / len(case.evidence)


def _build_parents(dataset: RagDataset) -> tuple[list[dict[str, str]], dict[str, ParentRecord]]:
    """Create overlapping 1024-token parents; KnowledgeBase creates 256/32 children inside them."""
    chunker = DocumentChunker()
    values: list[dict[str, str]] = []
    records: dict[str, ParentRecord] = {}
    for document in dataset.documents:
        parents = chunker.split(
            document.content,
            max_tokens=1024,
            overlap_tokens=128,
            strategy="fixed_tokens",
        )
        identity = hashlib.sha256(document.document_id.encode("utf-8")).hexdigest()[:16]
        for parent in parents:
            parent_id = f"parent-{identity}-{parent.chunk_index}"
            records[parent_id] = ParentRecord(
                parent_id=parent_id,
                source_id=document.document_id,
                title=document.title,
                text=parent.content,
                start_char=parent.start_char,
                end_char=parent.end_char,
            )
            values.append({"id": parent_id, "title": document.title, "content": parent.content})
    return values, records


def _transform_parent_hit(hit: Mapping[str, Any], parents: Mapping[str, ParentRecord]) -> dict[str, Any]:
    parent = parents[str(hit["document_id"])]
    child_start = parent.start_char + int(hit.get("source_start_char") or 0)
    child_end = parent.start_char + int(hit.get("source_end_char") or 0)
    return {
        **hit,
        "document_id": parent.source_id,
        # Retrieval success means that this child routes to a parent covering Gold.
        "source_start_char": parent.start_char,
        "source_end_char": parent.end_char,
        "child_source_start_char": child_start,
        "child_source_end_char": child_end,
        "parent_id": parent.parent_id,
        "parent_text": parent.text,
        "title": parent.title,
    }


def _capture_retrieval(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    *,
    parent_child: bool,
) -> list[dict[str, Any]]:
    cases = {case.case_id: case for case in dataset.cases}
    parent_values, parents = _build_parents(dataset) if parent_child else ([], {})
    documents = parent_values if parent_child else [
        {"id": document.document_id, "title": document.title, "content": document.content}
        for document in dataset.documents
    ]
    with tempfile.TemporaryDirectory(prefix="dialogpilot-context-topology-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded",
            chroma_path=temp_dir,
            sparse_index_path=str(Path(temp_dir) / "sparse.db"),
            load_default_docs=False,
            collection_name="dialogpilot_context_topology",
            chunk_strategy="fixed_tokens",
            chunk_max_tokens=256 if parent_child else 512,
            chunk_overlap_tokens=32 if parent_child else 64,
        )
        knowledge_base.add_documents(documents)
        captures = []
        for row in query_capture.get("rows") or ():
            case = cases[str(row["case_id"])]
            variants = _variants(row, "standalone")
            weights = _weights(
                variants, raw_mass=0.25, lexical_weight=0.75, vector_weight=0.25,
            )
            rankings: dict[str, list[str]] = {}
            hit_by_id: dict[str, dict[str, Any]] = {}
            started = time.perf_counter()
            for label, text, _vector_only in variants:
                for source, lexical, vector in (
                    ("bm25", 1.0, 0.0), ("vector", 0.0, 1.0),
                ):
                    hits = knowledge_base.search(text, top_k=CANDIDATE_K, retrieval_policy={
                        "lexical_weight": lexical,
                        "vector_weight": vector,
                        "rrf_k": 30,
                        "candidate_k": CANDIDATE_K,
                    })
                    transformed = [
                        _transform_parent_hit(hit, parents) if parent_child else dict(hit)
                        for hit in hits
                    ]
                    key = f"{label}:{source}"
                    rankings[key] = [str(hit["chunk_id"]) for hit in transformed]
                    hit_by_id.update((str(hit["chunk_id"]), hit) for hit in transformed)
            fused = fuse_rankings(
                rankings,
                weights=weights,
                rrf_k=10,
                top_k=CANDIDATE_K,
            )
            captures.append({
                "case": case,
                "fused_ids": fused,
                "hit_by_id": hit_by_id,
                "source_rankings": rankings,
                "query_for_rerank": str(row.get("standalone") or case.query),
                "retrieval_latency_ms": (time.perf_counter() - started) * 1000,
            })
        knowledge_base.close()
    return captures


def _capture_hierarchical_retrieval(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    hierarchy: HierarchyIndex,
) -> list[dict[str, Any]]:
    """Retrieve Haystack-produced leaves through the existing hybrid index."""
    cases = {case.case_id: case for case in dataset.cases}
    with tempfile.TemporaryDirectory(prefix="dialogpilot-hierarchical-topology-") as temp_dir:
        knowledge_base = KnowledgeBase(
            chroma_mode="embedded",
            chroma_path=temp_dir,
            sparse_index_path=str(Path(temp_dir) / "sparse.db"),
            load_default_docs=False,
            collection_name="dialogpilot_hierarchical_topology",
            # Haystack owns leaf boundaries. This ceiling prevents the storage
            # adapter from introducing a second, competing chunking contract.
            chunk_strategy="fixed_tokens",
            chunk_max_tokens=4096,
            chunk_overlap_tokens=0,
        )
        knowledge_base.add_documents(list(hierarchy.source_documents))
        captures = []
        for row in query_capture.get("rows") or ():
            case = cases[str(row["case_id"])]
            variants = _variants(row, "standalone")
            weights = _weights(
                variants, raw_mass=0.25, lexical_weight=0.75, vector_weight=0.25,
            )
            rankings: dict[str, list[str]] = {}
            hit_by_id: dict[str, dict[str, Any]] = {}
            started = time.perf_counter()
            for label, text, _vector_only in variants:
                for source, lexical, vector in (
                    ("bm25", 1.0, 0.0), ("vector", 0.0, 1.0),
                ):
                    hits = knowledge_base.search(text, top_k=CANDIDATE_K, retrieval_policy={
                        "lexical_weight": lexical,
                        "vector_weight": vector,
                        "rrf_k": 30,
                        "candidate_k": CANDIDATE_K,
                    })
                    transformed = [transform_leaf_hit(hit, hierarchy) for hit in hits]
                    key = f"{label}:{source}"
                    rankings[key] = [str(hit["chunk_id"]) for hit in transformed]
                    hit_by_id.update(
                        (str(hit["chunk_id"]), hit) for hit in transformed
                    )
            fused = fuse_rankings(rankings, weights=weights, rrf_k=10, top_k=CANDIDATE_K)
            captures.append({
                "case": case,
                "fused_ids": fused,
                "hit_by_id": hit_by_id,
                "source_rankings": rankings,
                "query_for_rerank": str(row.get("standalone") or case.query),
                "retrieval_latency_ms": (time.perf_counter() - started) * 1000,
            })
        knowledge_base.close()
    return captures


def _conditional_route(
    capture: Mapping[str, Any],
    document_lengths: Mapping[str, int],
) -> dict[str, Any]:
    """Route without Gold: long retrieved source plus first-rank branch disagreement."""
    top_documents = []
    for ranking in (capture.get("source_rankings") or {}).values():
        if not ranking:
            continue
        hit = capture["hit_by_id"].get(str(ranking[0]))
        if hit is not None:
            top_documents.append(str(hit.get("document_id") or ""))
    top_fused_documents = {
        str(capture["hit_by_id"][chunk_id].get("document_id") or "")
        for chunk_id in capture["fused_ids"][:FINAL_K]
    }
    long_document_seen = any(
        document_lengths.get(document_id, 0) >= LONG_DOCUMENT_CHARS
        for document_id in top_fused_documents
    )
    branch_disagreement = len(set(top_documents)) > 1
    use_parent_child = long_document_seen and branch_disagreement
    return {
        "selected_topology": (
            "parent-child-256-1024" if use_parent_child else "baseline-512"
        ),
        "long_document_seen": long_document_seen,
        "branch_first_rank_document_disagreement": branch_disagreement,
        "branch_first_rank_documents": top_documents,
        "policy": (
            "parent-child iff a baseline top-5 source is >=8000 chars and "
            "retrieval branches disagree on the first-ranked document"
        ),
    }


def _conditional_captures(
    dataset: RagDataset,
    baseline_captures: Sequence[Mapping[str, Any]],
    parent_captures: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    parent_by_case = {capture["case"].case_id: capture for capture in parent_captures}
    document_lengths = {
        document.document_id: len(document.content) for document in dataset.documents
    }
    selected = []
    for baseline in baseline_captures:
        route = _conditional_route(baseline, document_lengths)
        if route["selected_topology"] == "parent-child-256-1024":
            parent = parent_by_case[baseline["case"].case_id]
            capture = {
                **parent,
                # A cascade must pay for the baseline probe and the conditional retry.
                "retrieval_latency_ms": (
                    float(baseline["retrieval_latency_ms"])
                    + float(parent["retrieval_latency_ms"])
                ),
            }
        else:
            capture = dict(baseline)
        capture["route_decision"] = route
        selected.append(capture)
    return selected


async def _rerank(
    client: Any,
    profile: Any,
    captures: Sequence[Mapping[str, Any]],
    *,
    concurrency: int,
) -> list[dict[str, Any]]:
    reranker = ResultReranker(client, profile)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(capture: Mapping[str, Any]) -> dict[str, Any]:
        candidates = tuple(RerankCandidate(
            candidate_id=chunk_id,
            title=str(capture["hit_by_id"][chunk_id].get("title") or ""),
            text=str(capture["hit_by_id"][chunk_id].get("content") or ""),
        ) for chunk_id in capture["fused_ids"])
        async with semaphore:
            with capture_llm_usage() as usage:
                result = await reranker.rerank(str(capture["query_for_rerank"]), candidates)
        summary = usage.summary()["total"]
        return {
            **capture,
            "reranked_ids": list(result.ordered_ids),
            "rerank_error": result.error,
            "rerank_usage": summary,
        }

    return list(await asyncio.gather(*(one(capture) for capture in captures)))


def _reuse_reranks(
    captures: Sequence[Mapping[str, Any]],
    previous_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Reuse only an exact candidate-set capture; never project stale IDs into a new run."""
    by_case = {str(row["case_id"]): row for row in previous_rows}
    reused = []
    for capture in captures:
        case_id = capture["case"].case_id
        previous = by_case.get(case_id)
        previous_candidates = list((previous or {}).get("candidate_ids") or ())
        current_candidates = list(capture["fused_ids"])
        if previous is None or set(previous_candidates) != set(current_candidates):
            raise ValueError(f"rerank resume candidates differ for case={case_id}")
        reranked = list(previous.get("reranked_ids") or ())
        if previous.get("rerank_error"):
            # Invalid model output owns no ordering; fallback always means the
            # current first-stage order, even if a fresh Dense build changed ties.
            reranked = current_candidates
        if len(reranked) != len(current_candidates) or set(reranked) != set(current_candidates):
            raise ValueError(f"rerank resume is not an exact permutation for case={case_id}")
        reused.append({
            **capture,
            "reranked_ids": reranked,
            "rerank_error": previous.get("rerank_error"),
            "rerank_usage": dict(previous.get("rerank_usage") or _zero_usage()),
        })
    return reused


async def _rerank_with_resume(
    client: Any,
    profile: Any,
    captures: Sequence[Mapping[str, Any]],
    previous_rows: Sequence[Mapping[str, Any]],
    *,
    concurrency: int,
) -> list[dict[str, Any]]:
    """Reuse compatible cases and rerun only cases whose candidate set changed."""
    previous_by_case = {str(row["case_id"]): row for row in previous_rows}
    resolved: dict[str, dict[str, Any]] = {}
    pending = []
    for capture in captures:
        case_id = capture["case"].case_id
        previous = previous_by_case.get(case_id)
        if previous is None:
            pending.append(capture)
            continue
        try:
            resolved[case_id] = _reuse_reranks([capture], [previous])[0]
        except ValueError:
            pending.append(capture)
    for row in await _rerank(client, profile, pending, concurrency=concurrency):
        resolved[row["case"].case_id] = row
    return [resolved[capture["case"].case_id] for capture in captures]


def _baseline_chunks(dataset: RagDataset) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    chunks = project_chunks(
        dataset.documents, max_tokens=512, overlap_tokens=64, strategy="fixed_tokens",
    )
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    by_document: dict[str, list[Any]] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.document_id, []).append(chunk)
    return by_id, by_document


def _make_contexts(
    topology: str,
    capture: Mapping[str, Any],
    dataset: RagDataset,
    baseline_by_id: Mapping[str, Any],
    baseline_by_document: Mapping[str, Sequence[Any]],
    hierarchy: HierarchyIndex | None = None,
) -> tuple[ContextCandidate, ...]:
    if topology == "conditional-parent-child":
        topology = str(capture["route_decision"]["selected_topology"])
    documents = {document.document_id: document for document in dataset.documents}
    if topology in HIERARCHICAL_TOPOLOGIES:
        if hierarchy is None:
            raise ValueError(f"hierarchy is required for topology={topology}")
        if topology == "unique-parent-aggregation":
            candidates = list(unique_parent_candidates(
                capture,
                hierarchy,
                candidate_k=CANDIDATE_K,
                final_k=FINAL_K,
            ))
        elif topology == "dynamic-auto-merge":
            candidates = list(dynamic_auto_merge_candidates(
                capture, hierarchy, candidate_k=CANDIDATE_K,
            ))
        else:
            return budget_aware_mixed_candidates(
                capture,
                hierarchy,
                candidate_k=CANDIDATE_K,
                max_tokens=CONTEXT_MAX_TOKENS,
                max_chunks=FINAL_K,
            )
        return ContextPacker().pack(
            candidates,
            max_tokens=CONTEXT_MAX_TOKENS,
            max_chunks=FINAL_K,
            redundancy_threshold=1.0,
        ).selected

    candidates: list[ContextCandidate] = []
    seen = set()
    for rank, chunk_id in enumerate(capture["reranked_ids"][:FINAL_K], 1):
        hit = capture["hit_by_id"][chunk_id]
        if topology == "baseline-512":
            chunk = baseline_by_id[chunk_id]
            context_id = chunk.chunk_id
            source_id, start, end, text = (
                chunk.document_id, chunk.start_char, chunk.end_char, chunk.content,
            )
        elif topology == "neighbor-512":
            chunk = baseline_by_id[chunk_id]
            siblings = list(baseline_by_document[chunk.document_id])
            position = next(index for index, value in enumerate(siblings) if value.chunk_id == chunk_id)
            window = siblings[max(0, position - 1):position + 2]
            source_id = chunk.document_id
            start, end = min(item.start_char for item in window), max(item.end_char for item in window)
            text = documents[source_id].content[start:end]
            context_id = f"neighbor::{chunk_id}"
        elif topology == "parent-child-256-1024":
            parent_id = str(hit["parent_id"])
            source_id = str(hit["document_id"])
            start, end = int(hit["source_start_char"]), int(hit["source_end_char"])
            text = str(hit["parent_text"])
            context_id = f"parent::{parent_id}"
        else:
            raise ValueError(f"unknown topology: {topology}")
        identity = (source_id, start, end)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(ContextCandidate(
            chunk_id=context_id,
            document_id=source_id,
            text=text,
            start_char=start,
            end_char=end,
            title=str(hit.get("title") or ""),
            score=float(hit.get("score") or 0.0),
            ranks=(("rerank", rank),),
        ))
    packed = ContextPacker().pack(
        candidates,
        max_tokens=CONTEXT_MAX_TOKENS,
        max_chunks=FINAL_K,
        redundancy_threshold=1.0,
    )
    return packed.selected


async def _generate(
    client: Any,
    generator_profile: Any,
    judge_profile: Any,
    rows: Sequence[dict[str, Any]],
    *,
    concurrency: int,
) -> list[dict[str, Any]]:
    generator = GroundedAnswerGenerator(client, generator_profile)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        case: RagCase = row["case"]
        contexts: tuple[ContextCandidate, ...] = row["contexts"]
        async with semaphore:
            with capture_llm_usage() as generation_usage:
                answer = await generator.generate(case.query, contexts, history=case.history)
            by_id = {context.chunk_id: context for context in contexts}
            cited = [by_id[item].text for item in answer.citations if item in by_id]
            if answer.error is not None or answer.abstained:
                judgement, judge_error = None, "generation_not_answered"
                judge_summary = _zero_usage()
            else:
                with capture_llm_usage() as judge_usage:
                    judgement, judge_error = await _judge(
                        client,
                        judge_profile,
                        query=case.query,
                        answer=answer.answer,
                        reference=case.required_claims[0] if case.required_claims else "",
                        cited_context=cited,
                    )
                judge_summary = judge_usage.summary()["total"]
        gold_citations = sum(
            any(_covers(by_id[citation], evidence) for evidence in case.evidence)
            for citation in answer.citations if citation in by_id
        )
        return {
            **row,
            "answer": answer.answer,
            "citations": list(answer.citations),
            "claims": [
                {"text": claim.text, "citations": list(claim.citations)}
                for claim in answer.claims
            ],
            "abstained": answer.abstained,
            "generation_error": answer.error,
            "judge": judgement,
            "judge_error": judge_error,
            "gold_citation_precision": (
                gold_citations / len(answer.citations) if answer.citations else 0.0
            ),
            "generation_usage": generation_usage.summary()["total"],
            "judge_usage": judge_summary,
        }

    return list(await asyncio.gather(*(one(row) for row in rows)))


def _aggregate(topology: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    retrieval = [row["retrieval_metrics"] for row in rows]
    reranked = [row["reranked_metrics"] for row in rows]
    multi = [row for row in rows if len(row["case"].evidence) >= 2]
    successful = [
        row for row in rows
        if not row.get("generation_error") and not row.get("abstained")
    ]
    context_tokens = [float(row["context_tokens"]) for row in rows]
    pipeline_latencies = [
        float(row["retrieval_latency_ms"])
        + float(row["rerank_usage"]["latency_ms"]["sum"])
        + float(row.get("generation_usage", {}).get("latency_ms", {}).get("sum", 0))
        + float(row.get("judge_usage", {}).get("latency_ms", {}).get("sum", 0))
        for row in rows
    ]

    def mean(values: Sequence[float]) -> float:
        return statistics.fmean(values) if values else 0.0

    return {
        "topology": topology,
        "case_count": len(rows),
        "multi_condition_case_count": len(multi),
        "candidate_evidence_recall_at_20": mean([
            float(value["evidence_recall"]) for value in retrieval
        ]),
        "reranked_mrr_at_5": mean([float(value["mrr"]) for value in reranked]),
        "reranked_evidence_recall_at_5": mean([
            float(value["evidence_recall"]) for value in reranked
        ]),
        "context_evidence_recall": mean([float(row["context_recall"]) for row in rows]),
        "multi_condition_context_completeness": mean([
            float(row["context_recall"]) for row in multi
        ]),
        "mean_context_tokens": mean(context_tokens),
        "p95_context_tokens": _percentile(context_tokens, 0.95),
        "p95_pipeline_latency_ms": _percentile(pipeline_latencies, 0.95),
        "rerank_failure_rate": mean([float(bool(row["rerank_error"])) for row in rows]),
        # A typed evidence abstention is a valid generator outcome, not a protocol
        # failure. Keep it in the separate abstention rate; this gate measures only
        # provider/structured-output/validation terminal errors.
        "generation_failure_rate": mean([
            float(bool(row.get("generation_error"))) for row in rows
        ]),
        "generation_error_counts": {
            name: sum(str(row.get("generation_error") or "") == name for row in rows)
            for name in sorted({
                str(row.get("generation_error") or "") for row in rows
                if row.get("generation_error")
            })
        },
        "abstention_rate": mean([float(bool(row.get("abstained"))) for row in rows]),
        "judge_failure_rate": mean([
            float(row.get("judge") is None) for row in successful
        ]),
        "claim_support_rate": mean([
            float(bool(
                not row.get("generation_error")
                and not row.get("abstained")
                and row.get("judge")
                and row["judge"]["grounded"]
            )) for row in rows
        ]),
        "citation_correctness_rate": mean([
            float(bool(
                not row.get("generation_error")
                and not row.get("abstained")
                and row.get("judge")
                and row["judge"]["citations_relevant"]
            )) for row in rows
        ]),
        "answer_completeness_rate": mean([
            float(bool(
                not row.get("generation_error")
                and not row.get("abstained")
                and row.get("judge")
                and row["judge"]["complete"]
            )) for row in rows
        ]),
        "gold_citation_precision": mean([
            float(row.get("gold_citation_precision", 0.0))
            if row in successful else 0.0 for row in rows
        ]),
    }


def _zero_usage() -> dict[str, Any]:
    return {
        "calls": 0,
        "errors": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "latency_ms": {"p50": 0.0, "p95": 0.0, "max": 0.0, "sum": 0.0},
    }


def _prepare_structural_rows(
    topology: str,
    captures: Sequence[Mapping[str, Any]],
    dataset: RagDataset,
    baseline_chunks: Mapping[str, Any],
    baseline_by_document: Mapping[str, Sequence[Any]],
    hierarchy: HierarchyIndex | None = None,
) -> list[dict[str, Any]]:
    """Use first-stage order for the cheap precheck; no LLM result is implied."""
    token_estimator = TokenEstimator()
    rows = []
    for value in captures:
        capture = {
            **value,
            "reranked_ids": list(value["fused_ids"]),
            "rerank_error": None,
            "rerank_usage": _zero_usage(),
        }
        case: RagCase = capture["case"]
        contexts = _make_contexts(
            topology,
            capture,
            dataset,
            baseline_chunks,
            baseline_by_document,
            hierarchy,
        )
        rows.append({
            **capture,
            "contexts": contexts,
            "context_tokens": token_estimator.estimate(
                "\n\n".join(context.text for context in contexts)
            ),
            "context_recall": _context_recall(case, contexts),
            "retrieval_metrics": evaluate_ranked_hits(
                case, capture["fused_ids"], capture["hit_by_id"], top_k=CANDIDATE_K,
            ),
            "reranked_metrics": evaluate_ranked_hits(
                case, capture["fused_ids"], capture["hit_by_id"], top_k=FINAL_K,
            ),
        })
    return rows


def _aggregate_structural(topology: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    multi = [row for row in rows if len(row["case"].evidence) >= 2]

    def mean(values: Sequence[float]) -> float:
        return statistics.fmean(values) if values else 0.0

    context_tokens = [float(row["context_tokens"]) for row in rows]
    retrieval_latencies = [float(row["retrieval_latency_ms"]) for row in rows]
    result = {
        "topology": topology,
        "case_count": len(rows),
        "multi_condition_case_count": len(multi),
        "candidate_evidence_recall_at_20": mean([
            float(row["retrieval_metrics"]["evidence_recall"]) for row in rows
        ]),
        "first_stage_mrr_at_5": mean([
            float(row["reranked_metrics"]["mrr"]) for row in rows
        ]),
        "first_stage_evidence_recall_at_5": mean([
            float(row["reranked_metrics"]["evidence_recall"]) for row in rows
        ]),
        "context_evidence_recall": mean([float(row["context_recall"]) for row in rows]),
        "multi_condition_context_completeness": mean([
            float(row["context_recall"]) for row in multi
        ]),
        "mean_context_tokens": mean(context_tokens),
        "p95_context_tokens": _percentile(context_tokens, 0.95),
        "p95_retrieval_latency_ms": _percentile(retrieval_latencies, 0.95),
    }
    routed = [
        row for row in rows
        if (row.get("route_decision") or {}).get("selected_topology")
        == "parent-child-256-1024"
    ]
    if topology == "conditional-parent-child":
        result["parent_child_route_count"] = len(routed)
        result["parent_child_route_rate"] = len(routed) / len(rows) if rows else 0.0
    return result


def _aggregate_rerank_stage(
    topology: str, rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    multi = [row for row in rows if len(row["case"].evidence) >= 2]

    def mean(values: Sequence[float]) -> float:
        return statistics.fmean(values) if values else 0.0

    context_tokens = [float(row["context_tokens"]) for row in rows]
    latencies = [
        float(row["retrieval_latency_ms"])
        + float(row["rerank_usage"]["latency_ms"]["sum"])
        for row in rows
    ]
    return {
        "topology": topology,
        "case_count": len(rows),
        "multi_condition_case_count": len(multi),
        "candidate_evidence_recall_at_20": mean([
            float(row["retrieval_metrics"]["evidence_recall"]) for row in rows
        ]),
        "reranked_mrr_at_5": mean([
            float(row["reranked_metrics"]["mrr"]) for row in rows
        ]),
        "reranked_evidence_recall_at_5": mean([
            float(row["reranked_metrics"]["evidence_recall"]) for row in rows
        ]),
        "context_evidence_recall": mean([float(row["context_recall"]) for row in rows]),
        "multi_condition_context_completeness": mean([
            float(row["context_recall"]) for row in multi
        ]),
        "mean_context_tokens": mean(context_tokens),
        "p95_context_tokens": _percentile(context_tokens, 0.95),
        "p95_retrieval_rerank_latency_ms": _percentile(latencies, 0.95),
        "rerank_failure_rate": mean([
            float(bool(row["rerank_error"])) for row in rows
        ]),
        "rerank_input_tokens": sum(
            int(row["rerank_usage"].get("input_tokens", 0)) for row in rows
        ),
        "rerank_output_tokens": sum(
            int(row["rerank_usage"].get("output_tokens", 0)) for row in rows
        ),
    }


def _structural_gates(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any], *, harmful_rate: float,
) -> dict[str, bool]:
    epsilon = 1e-12
    values = {
        "candidate_recall_at_20_not_lower": (
            candidate["candidate_evidence_recall_at_20"] + epsilon
            >= baseline["candidate_evidence_recall_at_20"]
        ),
        "first_stage_mrr_at_5_not_lower": (
            candidate["first_stage_mrr_at_5"] + epsilon >= baseline["first_stage_mrr_at_5"]
        ),
        "multi_condition_completeness_improves": (
            candidate["multi_condition_context_completeness"]
            > baseline["multi_condition_context_completeness"] + epsilon
        ),
        "context_p95_within_2600": candidate["p95_context_tokens"] <= CONTEXT_MAX_TOKENS,
        "retrieval_p95_latency_growth_le_20pct": (
            candidate["p95_retrieval_latency_ms"]
            <= baseline["p95_retrieval_latency_ms"] * 1.20
        ),
        "harmful_context_rate_does_not_increase": harmful_rate == 0.0,
    }
    values["passes_all"] = all(values.values())
    return values


def run_structural_ablation(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    *,
    topologies: Sequence[str] = DEFAULT_TOPOLOGIES,
) -> dict[str, Any]:
    """Run cheap deterministic gates before spending reranker/generator/Judge calls."""
    if query_capture.get("dataset_id") != dataset.manifest.get("dataset_id"):
        raise ValueError("query capture and dataset IDs do not match")
    unknown = set(topologies) - set(TOPOLOGIES)
    if unknown:
        raise ValueError(f"unknown topologies: {sorted(unknown)}")
    baseline_chunks, baseline_by_document = _baseline_chunks(dataset)
    needs_baseline = bool({
        "baseline-512", "neighbor-512", "conditional-parent-child",
    }.intersection(topologies))
    baseline_capture = (
        _capture_retrieval(dataset, query_capture, parent_child=False)
        if needs_baseline else []
    )
    needs_parent = bool(
        {"parent-child-256-1024", "conditional-parent-child"}.intersection(topologies)
    )
    parent_capture = (
        _capture_retrieval(dataset, query_capture, parent_child=True)
        if needs_parent else []
    )
    conditional_capture = (
        _conditional_captures(dataset, baseline_capture, parent_capture)
        if "conditional-parent-child" in topologies else []
    )
    hierarchy = build_hierarchy(dataset.documents) if HIERARCHICAL_TOPOLOGIES.intersection(
        topologies
    ) else None
    hierarchical_capture = (
        _capture_hierarchical_retrieval(dataset, query_capture, hierarchy)
        if hierarchy is not None else []
    )
    rows_by_topology = {}
    for topology in topologies:
        if topology == "parent-child-256-1024":
            captures = parent_capture
        elif topology == "conditional-parent-child":
            captures = conditional_capture
        elif topology in HIERARCHICAL_TOPOLOGIES:
            captures = hierarchical_capture
        else:
            captures = baseline_capture
        rows_by_topology[topology] = _prepare_structural_rows(
            topology,
            captures,
            dataset,
            baseline_chunks,
            baseline_by_document,
            hierarchy,
        )
    summaries = {
        topology: _aggregate_structural(topology, rows)
        for topology, rows in rows_by_topology.items()
    }
    baseline = summaries.get("baseline-512")
    harmful = {}
    gates = {}
    if baseline is not None:
        baseline_rows = {
            row["case"].case_id: row for row in rows_by_topology["baseline-512"]
        }
        for topology, rows in rows_by_topology.items():
            if topology == "baseline-512":
                continue
            harmful[topology] = statistics.fmean(
                float(row["context_recall"] < baseline_rows[row["case"].case_id]["context_recall"])
                for row in rows
            )
            gates[topology] = _structural_gates(
                summaries[topology], baseline, harmful_rate=harmful[topology],
            )
    eligible = [topology for topology, value in gates.items() if value["passes_all"]]
    return {
        "schema_version": 2,
        "stage": "structural_precheck",
        "dataset_id": dataset.manifest["dataset_id"],
        "split": query_capture["split"],
        "case_count": len(query_capture.get("rows") or ()),
        "ranking_contract": (
            f"{_query_contract(query_capture)}; first-stage only; MRR@5 is not LLM-reranked"
        ),
        "summaries": summaries,
        "harmful_context_rate_vs_baseline": harmful,
        "structural_gates_vs_baseline": gates,
        "eligible_for_model_stage": eligible,
        "model_stage": {
            "status": "not_run",
            "required_metrics": [
                "reranked MRR@5", "claim support", "citation correctness", "generation latency",
            ],
        },
        "conditional_route_contract": (
            "parent-child iff a baseline top-5 source is >=8000 chars and retrieval "
            "branches disagree on the first-ranked document; no Gold labels are used"
        ),
        "hierarchical_contract": hierarchy_contract(),
        "rows": {
            topology: [_public_row(row) for row in rows]
            for topology, rows in rows_by_topology.items()
        },
        "limitations": [
            _grounding_limitation(dataset),
            "This precheck cannot establish generation or citation quality.",
            "Latency is serial local retrieval on a small corpus, not a load test.",
        ],
    }


def _candidate_gates(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, bool]:
    epsilon = 1e-12
    return {
        "candidate_recall_at_20_not_lower": (
            candidate["candidate_evidence_recall_at_20"] + epsilon
            >= baseline["candidate_evidence_recall_at_20"]
        ),
        "reranked_mrr_at_5_not_lower": (
            candidate["reranked_mrr_at_5"] + epsilon >= baseline["reranked_mrr_at_5"]
        ),
        "multi_condition_completeness_improves": (
            candidate["multi_condition_context_completeness"]
            > baseline["multi_condition_context_completeness"] + epsilon
        ),
        "claim_support_not_lower": (
            candidate["claim_support_rate"] + epsilon >= baseline["claim_support_rate"]
        ),
        "citation_correctness_not_lower": (
            candidate["citation_correctness_rate"] + epsilon
            >= baseline["citation_correctness_rate"]
        ),
        "context_p95_within_2600": candidate["p95_context_tokens"] <= CONTEXT_MAX_TOKENS,
        "pipeline_p95_latency_growth_le_20pct": (
            candidate["p95_pipeline_latency_ms"]
            <= baseline["p95_pipeline_latency_ms"] * 1.20
        ),
        "rerank_failure_rate_le_1pct": candidate["rerank_failure_rate"] <= 0.01,
        "generation_failure_rate_le_1pct": candidate["generation_failure_rate"] <= 0.01,
        "judge_failure_rate_le_5pct": candidate["judge_failure_rate"] <= 0.05,
    }


def _rerank_stage_gates(
    candidate: Mapping[str, Any], baseline: Mapping[str, Any],
) -> dict[str, bool]:
    epsilon = 1e-12
    return {
        "candidate_recall_at_20_not_lower": (
            candidate["candidate_evidence_recall_at_20"] + epsilon
            >= baseline["candidate_evidence_recall_at_20"]
        ),
        "reranked_mrr_at_5_not_lower": (
            candidate["reranked_mrr_at_5"] + epsilon >= baseline["reranked_mrr_at_5"]
        ),
        "multi_condition_completeness_improves": (
            candidate["multi_condition_context_completeness"]
            > baseline["multi_condition_context_completeness"] + epsilon
        ),
        "context_p95_within_2600": candidate["p95_context_tokens"] <= CONTEXT_MAX_TOKENS,
        "rerank_failure_rate_le_1pct": candidate["rerank_failure_rate"] <= 0.01,
        "retrieval_rerank_p95_latency_growth_le_20pct": (
            candidate["p95_retrieval_rerank_latency_ms"]
            <= baseline["p95_retrieval_rerank_latency_ms"] * 1.20
        ),
    }


def _public_row(row: Mapping[str, Any]) -> dict[str, Any]:
    case: RagCase = row["case"]
    contexts: Sequence[ContextCandidate] = row["contexts"]
    return {
        "case_id": case.case_id,
        "group_id": case.group_id,
        "evidence_count": len(case.evidence),
        "candidate_ids": list(row["fused_ids"]),
        "reranked_ids": list(row["reranked_ids"]),
        "context_spans": [{
            "context_id": context.chunk_id,
            "source_id": context.document_id,
            "start_char": context.start_char,
            "end_char": context.end_char,
        } for context in contexts],
        "context_tokens": row["context_tokens"],
        "context_recall": row["context_recall"],
        "retrieval_metrics": row["retrieval_metrics"],
        "reranked_metrics": row["reranked_metrics"],
        "retrieval_latency_ms": row["retrieval_latency_ms"],
        "rerank_error": row["rerank_error"],
        "answer": row.get("answer", ""),
        "citations": row.get("citations", []),
        "claims": row.get("claims", []),
        "abstained": row.get("abstained", False),
        "generation_error": row.get("generation_error"),
        "judge": row.get("judge"),
        "judge_error": row.get("judge_error"),
        "gold_citation_precision": row.get("gold_citation_precision", 0.0),
        "rerank_usage": row["rerank_usage"],
        "route_decision": row.get("route_decision"),
        "generation_usage": row.get("generation_usage", {}),
        "judge_usage": row.get("judge_usage", {}),
    }


async def run_ablation(
    dataset: RagDataset,
    query_capture: Mapping[str, Any],
    *,
    topologies: Sequence[str] = DEFAULT_TOPOLOGIES,
    concurrency: int = 3,
    previous_report: Mapping[str, Any] | None = None,
    rerank_only: bool = False,
) -> dict[str, Any]:
    if query_capture.get("dataset_id") != dataset.manifest.get("dataset_id"):
        raise ValueError("query capture and dataset IDs do not match")
    if previous_report is not None and previous_report.get("dataset_id") != dataset.manifest.get("dataset_id"):
        raise ValueError("previous report and dataset IDs do not match")
    unknown = set(topologies) - set(TOPOLOGIES)
    if unknown:
        raise ValueError(f"unknown topologies: {sorted(unknown)}")
    policy = ModelPolicy.from_env()
    if previous_report is not None:
        previous_prompt = str(
            previous_report.get("experiment_contract", {})
            .get("prompt_versions", {})
            .get("rerank") or ""
        )
        if previous_prompt != RERANK_PROMPT_VERSION:
            raise ValueError(
                "previous report rerank prompt differs from the current contract"
            )
        previous_rerank_model = (
            previous_report.get("models", {}).get("roles", {}).get("rerank")
        )
        if previous_rerank_model != policy.profile(ModelRole.RERANK).to_dict():
            raise ValueError(
                "previous report rerank model differs from the current contract"
            )
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    kwargs: dict[str, Any] = {"api_key": api_key}
    if policy.base_url:
        kwargs["base_url"] = policy.base_url
    client = AsyncAnthropic(**kwargs)
    baseline_chunks, baseline_by_document = _baseline_chunks(dataset)
    token_estimator = TokenEstimator()
    needs_baseline = bool({
        "baseline-512", "neighbor-512", "conditional-parent-child",
    }.intersection(topologies))
    baseline_capture = (
        _capture_retrieval(dataset, query_capture, parent_child=False)
        if needs_baseline else []
    )
    needs_parent = bool(
        {"parent-child-256-1024", "conditional-parent-child"}.intersection(topologies)
    )
    parent_capture = (
        _capture_retrieval(dataset, query_capture, parent_child=True)
        if needs_parent else []
    )
    hierarchy = build_hierarchy(dataset.documents) if HIERARCHICAL_TOPOLOGIES.intersection(
        topologies
    ) else None
    hierarchical_capture = (
        _capture_hierarchical_retrieval(dataset, query_capture, hierarchy)
        if hierarchy is not None else []
    )
    previous_rows = dict((previous_report or {}).get("rows") or {})
    baseline_ranked = await _rerank_with_resume(
        client,
        policy.profile(ModelRole.RERANK),
        baseline_capture,
        previous_rows.get("baseline-512", ()),
        concurrency=concurrency,
    ) if baseline_capture else []
    parent_ranked = await _rerank_with_resume(
        client,
        policy.profile(ModelRole.RERANK),
        parent_capture,
        previous_rows.get("parent-child-256-1024", ()),
        concurrency=concurrency,
    ) if parent_capture else []
    conditional_ranked = (
        _conditional_captures(dataset, baseline_ranked, parent_ranked)
        if "conditional-parent-child" in topologies else []
    )
    hierarchical_previous = next((
        previous_rows.get(name, ()) for name in HIERARCHICAL_TOPOLOGIES
        if previous_rows.get(name)
    ), ())
    hierarchical_ranked = await _rerank_with_resume(
        client,
        policy.profile(ModelRole.RERANK),
        hierarchical_capture,
        hierarchical_previous,
        concurrency=concurrency,
    ) if hierarchical_capture else []
    rows_by_topology: dict[str, list[dict[str, Any]]] = {}
    for topology in topologies:
        if topology == "parent-child-256-1024":
            captures = parent_ranked
        elif topology == "conditional-parent-child":
            captures = conditional_ranked
        elif topology in HIERARCHICAL_TOPOLOGIES:
            captures = hierarchical_ranked
        else:
            captures = baseline_ranked
        rows = []
        for capture in captures:
            case: RagCase = capture["case"]
            contexts = _make_contexts(
                topology,
                capture,
                dataset,
                baseline_chunks,
                baseline_by_document,
                hierarchy,
            )
            rows.append({
                **capture,
                "contexts": contexts,
                "context_tokens": token_estimator.estimate(
                    "\n\n".join(context.text for context in contexts)
                ),
                "context_recall": _context_recall(case, contexts),
                "retrieval_metrics": evaluate_ranked_hits(
                    case, capture["fused_ids"], capture["hit_by_id"], top_k=CANDIDATE_K,
                ),
                "reranked_metrics": evaluate_ranked_hits(
                    case, capture["reranked_ids"], capture["hit_by_id"], top_k=FINAL_K,
                ),
            })
        rows_by_topology[topology] = (
            rows if rerank_only else await _generate(
                client,
                policy.profile(ModelRole.SYNTHESIS),
                policy.profile(ModelRole.JUDGE),
                rows,
                concurrency=concurrency,
            )
        )
    summaries = {
        name: (
            _aggregate_rerank_stage(name, rows)
            if rerank_only else _aggregate(name, rows)
        )
        for name, rows in rows_by_topology.items()
    }
    baseline = summaries.get("baseline-512")
    gates = {}
    harmful = {}
    if baseline is not None:
        baseline_rows = {
            row["case"].case_id: row for row in rows_by_topology["baseline-512"]
        }
        for topology, summary in summaries.items():
            if topology == "baseline-512":
                continue
            candidate_rows = rows_by_topology[topology]
            harmful[topology] = statistics.fmean(
                float(row["context_recall"] < baseline_rows[row["case"].case_id]["context_recall"])
                for row in candidate_rows
            )
            gates[topology] = (
                _rerank_stage_gates(summary, baseline)
                if rerank_only else _candidate_gates(summary, baseline)
            )
            gates[topology]["harmful_context_rate_does_not_increase"] = harmful[topology] == 0.0
            gates[topology]["passes_all"] = all(gates[topology].values())
    eligible = [name for name, values in gates.items() if values["passes_all"]]
    recommended = "baseline-512" if baseline is not None else None
    if eligible:
        recommended = sorted(eligible, key=lambda name: (
            -summaries[name]["multi_condition_context_completeness"],
            summaries[name]["p95_context_tokens"],
            summaries[name][
                "p95_retrieval_rerank_latency_ms"
                if rerank_only else "p95_pipeline_latency_ms"
            ],
            name,
        ))[0]
    return {
        "schema_version": 1,
        "stage": "rerank_and_packing" if rerank_only else "full_chain",
        "dataset_id": dataset.manifest["dataset_id"],
        "split": query_capture["split"],
        "case_count": len(query_capture.get("rows") or ()),
        "experiment_contract": {
            "baseline": "retrieve/rerank fixed 512/64 chunks; pack top-5 within 2600 tokens",
            "neighbor": "same anchors as baseline; expand each to previous/current/next sibling",
            "parent_child": (
                "retrieve/rerank 256/32 children nested under 1024/128 parents; "
                "deliver deduplicated parents"
            ),
            "conditional_parent_child": (
                "run baseline first; run/deliver parent-child only when a baseline top-5 "
                "source is >=8000 chars and retrieval branches disagree on the first document"
            ),
            "unique_parent_aggregation": (
                "retrieve/rerank Haystack hierarchy leaves Top-20; score each unique "
                "1024-level parent by sum reciprocal child ranks before parent Top-5"
            ),
            "dynamic_auto_merge": (
                "Haystack AutoMergingRetriever threshold=.5 promotes sibling hits; "
                "isolated hits retain their smaller granularity"
            ),
            "budget_aware_mixed": (
                "pack the auto-merged 256/512/1024 mix by marginal retrieved-evidence "
                "rank score under the token budget; descend when a preferred parent "
                "cannot fit and use remaining budget for isolated-hit windows"
            ),
            "hierarchical_component_contract": hierarchy_contract(),
            "fixed_query": _query_contract(query_capture),
            "prompt_versions": {
                "rerank": RERANK_PROMPT_VERSION,
                "generation": GROUNDED_GENERATION_PROMPT_VERSION,
                "judge": JUDGE_PROMPT_VERSION,
            },
            "selection": "hard gates first; no weighted aggregate score",
        },
        "models": policy.to_dict(),
        "rerank_capture": {
            "mode": (
                "exact-candidate-set resume; changed candidate sets rerun"
                if previous_report is not None else "fresh"
            ),
            "previous_report_dataset_id": (
                previous_report.get("dataset_id") if previous_report is not None else None
            ),
        },
        "summaries": summaries,
        "harmful_context_rate_vs_baseline": harmful,
        "gates_vs_baseline": gates,
        "recommended": recommended,
        "rows": {
            topology: [_public_row(row) for row in rows]
            for topology, rows in rows_by_topology.items()
        },
        "limitations": [
            _grounding_limitation(dataset),
            (
                "Generation and Judge were not run; this stage establishes only rerank and packing."
                if rerank_only else
                "LLM Judge is uncalibrated against a fresh human-labelled sample."
            ),
            "P95 includes provider variance and this small sample is not a load test.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("query_capture", type=Path)
    parser.add_argument(
        "--topologies",
        nargs="+",
        choices=TOPOLOGIES,
        default=list(DEFAULT_TOPOLOGIES),
    )
    parser.add_argument("--concurrency", type=int, default=3)
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument(
        "--structural-only", action="store_true",
        help="Run deterministic pre-gates without reranker, generator, or Judge calls",
    )
    stage.add_argument(
        "--rerank-only", action="store_true",
        help="Run and persist reranker plus packing gates without generation or Judge",
    )
    parser.add_argument(
        "--resume-report", type=Path,
        help="Reuse rerank outputs only when every case has the exact same candidate set",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = RagDataset.load(args.dataset)
    capture = json.loads(args.query_capture.read_text(encoding="utf-8"))
    previous_report = (
        json.loads(args.resume_report.read_text(encoding="utf-8"))
        if args.resume_report else None
    )
    report = (
        run_structural_ablation(dataset, capture, topologies=args.topologies)
        if args.structural_only else
        asyncio.run(run_ablation(
            dataset, capture, topologies=args.topologies, concurrency=args.concurrency,
            previous_report=previous_report,
            rerank_only=args.rerank_only,
        ))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "dataset_id": report["dataset_id"],
        "split": report["split"],
        "case_count": report["case_count"],
        "recommended": report.get("recommended"),
        "summaries": report["summaries"],
        "gates_vs_baseline": (
            report.get("gates_vs_baseline") or report.get("structural_gates_vs_baseline")
        ),
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
