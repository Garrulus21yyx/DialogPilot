"""Deterministic metrics that preserve attribution between RAG stages."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping, Sequence

from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument, StageTrace
from mcp.document_chunker import ChunkStrategy, DocumentChunk, DocumentChunker


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    document_id: str
    content: str
    start_char: int
    end_char: int


def project_chunks(
    documents: Sequence[RagDocument],
    *,
    max_tokens: int,
    overlap_tokens: int,
    strategy: ChunkStrategy | str,
) -> list[IndexedChunk]:
    """Create experiment chunks through the same chunk owner as production ingestion."""
    chunker = DocumentChunker()
    result: list[IndexedChunk] = []
    for document in documents:
        for chunk in chunker.split(
            document.content,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            strategy=strategy,
        ):
            result.append(IndexedChunk(
                chunk_id=f"{document.document_id}::chunk-{chunk.chunk_index}",
                document_id=document.document_id,
                content=chunk.content,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
            ))
    return result


def chunk_contains_evidence(chunk: IndexedChunk, evidence: EvidenceSpan) -> bool:
    return (
        chunk.document_id == evidence.document_id
        and chunk.start_char <= evidence.start_char
        and chunk.end_char >= evidence.end_char
    )


def chunk_intersects_evidence(chunk: IndexedChunk, evidence: EvidenceSpan) -> bool:
    return (
        chunk.document_id == evidence.document_id
        and chunk.start_char < evidence.end_char
        and chunk.end_char > evidence.start_char
    )


def evaluate_chunk_projection(
    documents: Sequence[RagDocument],
    cases: Sequence[RagCase],
    *,
    max_tokens: int,
    overlap_tokens: int,
    strategy: ChunkStrategy | str,
) -> tuple[dict[str, float], list[IndexedChunk]]:
    """Measure whether preprocessing preserves gold evidence before retrieval runs."""
    chunks = project_chunks(
        documents,
        max_tokens=max_tokens,
        overlap_tokens=overlap_tokens,
        strategy=strategy,
    )
    evidence = [item for case in cases for item in case.evidence]
    contained = sum(any(chunk_contains_evidence(chunk, item) for chunk in chunks) for item in evidence)
    fragmented = sum(
        not any(chunk_contains_evidence(chunk, item) for chunk in chunks)
        and any(chunk_intersects_evidence(chunk, item) for chunk in chunks)
        for item in evidence
    )
    source_chars = sum(len(document.content.strip()) for document in documents)
    indexed_chars = sum(len(chunk.content) for chunk in chunks)
    return ({
        "evidence_count": float(len(evidence)),
        "evidence_containment_rate": contained / len(evidence) if evidence else 1.0,
        "boundary_fragmentation_rate": fragmented / len(evidence) if evidence else 0.0,
        "index_amplification": indexed_chars / source_chars if source_chars else 1.0,
        "duplicate_char_ratio": max(0.0, indexed_chars - source_chars) / source_chars if source_chars else 0.0,
        "chunks_per_document": len(chunks) / len(documents) if documents else 0.0,
    }, chunks)


def _evidence_chunk_ids(
    evidence: Iterable[EvidenceSpan], chunks: Mapping[str, IndexedChunk],
) -> set[str]:
    return {
        chunk_id
        for chunk_id, chunk in chunks.items()
        if any(chunk_contains_evidence(chunk, item) for item in evidence)
    }


def _recall(ranked_ids: Sequence[str], relevant_ids: set[str]) -> float:
    if not relevant_ids:
        return 1.0
    return len(set(ranked_ids) & relevant_ids) / len(relevant_ids)


def evaluate_stage_trace(
    case: RagCase,
    trace: StageTrace,
    chunks: Sequence[IndexedChunk],
) -> dict[str, float]:
    """Show exactly which stage retained or dropped authoritative evidence."""
    if trace.case_id != case.case_id:
        raise ValueError("trace and case IDs do not match")
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    relevant = _evidence_chunk_ids(case.evidence, by_id)
    fused_recall = _recall(trace.fused_chunk_ids, relevant)
    reranked_recall = _recall(trace.reranked_chunk_ids, relevant)
    packed_recall = _recall(trace.packed_chunk_ids, relevant)
    normalized_answer = " ".join(trace.answer.lower().split())
    required_covered = sum(
        " ".join(claim.lower().split()) in normalized_answer for claim in case.required_claims
    )
    forbidden_present = sum(
        " ".join(claim.lower().split()) in normalized_answer for claim in case.forbidden_claims
    )
    return {
        "first_stage_evidence_recall": fused_recall,
        "reranked_evidence_recall": reranked_recall,
        "packed_evidence_recall": packed_recall,
        "rerank_harmful": float(fused_recall > reranked_recall),
        "packing_harmful": float(reranked_recall > packed_recall),
        "required_claim_coverage": (
            required_covered / len(case.required_claims) if case.required_claims else 1.0
        ),
        "forbidden_claim_rate": (
            forbidden_present / len(case.forbidden_claims) if case.forbidden_claims else 0.0
        ),
        "citation_precision": (
            len(set(trace.citations) & {item.document_id for item in case.evidence})
            / len(set(trace.citations))
            if trace.citations else (1.0 if not case.answerable else 0.0)
        ),
    }


def evaluate_ranked_hits(
    case: RagCase,
    ranked_ids: Sequence[str],
    hit_by_id: Mapping[str, Mapping[str, object]],
    *,
    top_k: int,
) -> dict[str, float]:
    """Evaluate retrieved chunk projections against source-coordinate evidence."""
    ranked = list(ranked_ids[:top_k])

    def covers(hit: Mapping[str, object], evidence: EvidenceSpan) -> bool:
        return (
            str(hit.get("document_id")) == evidence.document_id
            and int(hit.get("source_start_char", 0)) <= evidence.start_char
            and int(hit.get("source_end_char", 0)) >= evidence.end_char
        )

    covered = {
        index for index, evidence in enumerate(case.evidence)
        if any(covers(hit_by_id[item], evidence) for item in ranked if item in hit_by_id)
    }
    relevant_ranks = [
        rank for rank, item in enumerate(ranked, 1)
        if item in hit_by_id and any(covers(hit_by_id[item], evidence) for evidence in case.evidence)
    ]
    relevant_documents = {evidence.document_id for evidence in case.evidence}
    retrieved_documents = {
        str(hit_by_id[item].get("document_id")) for item in ranked if item in hit_by_id
    }
    dcg = sum(1.0 / math.log2(rank + 1) for rank in relevant_ranks)
    ideal_count = min(len(relevant_documents), top_k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return {
        "evidence_recall": len(covered) / len(case.evidence) if case.evidence else 1.0,
        "document_recall": (
            len(retrieved_documents & relevant_documents) / len(relevant_documents)
            if relevant_documents else 1.0
        ),
        "mrr": 1.0 / relevant_ranks[0] if relevant_ranks else 0.0,
        "ndcg": dcg / ideal if ideal else 1.0,
    }
