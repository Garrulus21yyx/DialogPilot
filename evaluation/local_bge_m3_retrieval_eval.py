"""Fully local first-stage retrieval ablations over evidence-span RAG data."""
from __future__ import annotations

import math
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np
import torch

from application.chinese_lexical import postgres_lexical_document
from application.knowledge_retrieval_text import build_child_retrieval_text
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from mcp.document_chunker import ChunkStrategy, DocumentChunker
from mcp.rank_fusion import fuse_rankings


@dataclass(frozen=True)
class LocalChunk:
    chunk_id: str
    document_id: str
    start_char: int
    end_char: int
    retrieval_text: str


def build_local_chunks(dataset, *, max_tokens: int = 512, overlap_tokens: int = 64):
    chunker = DocumentChunker()
    chunks = []
    for document in dataset.documents:
        for chunk in chunker.split(
            document.content,
            max_tokens=max_tokens,
            overlap_tokens=overlap_tokens,
            strategy=ChunkStrategy.STRUCTURE_AWARE,
        ):
            chunks.append(LocalChunk(
                chunk_id=f"{document.document_id}::chunk-{chunk.chunk_index}",
                document_id=document.document_id,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                retrieval_text=build_child_retrieval_text(
                    title=document.title,
                    section_path=chunk.section_path,
                    content=chunk.content,
                ),
            ))
    return tuple(chunks)


def deterministic_query(case, mode: str) -> str:
    if mode == "raw":
        return case.query
    if mode == "user_history":
        # Doc2Dial histories alternate user/agent and begin with the user. This
        # conservative projection cannot copy prior agent wording into search.
        values = case.history[::2]
    elif mode == "history":
        values = case.history
    else:
        raise ValueError("query mode must be raw, user_history, or history")
    history = tuple(item.strip() for item in values[-4:] if item.strip())
    if not history:
        return case.query
    return "\n".join(("[CONVERSATION]", *history, "[CURRENT]", case.query))


def bm25_matrix(queries: Sequence[str], documents: Sequence[str]) -> np.ndarray:
    tokenized = [postgres_lexical_document(text).split() for text in documents]
    counters = [Counter(tokens) for tokens in tokenized]
    lengths = np.asarray([len(tokens) for tokens in tokenized], dtype=np.float32)
    average_length = max(float(lengths.mean()), 1.0)
    document_frequency = Counter(
        token for tokens in tokenized for token in set(tokens)
    )
    count = len(documents)
    scores = np.zeros((len(queries), count), dtype=np.float32)
    for query_index, query in enumerate(queries):
        for token in dict.fromkeys(postgres_lexical_document(query).split()):
            df = document_frequency.get(token, 0)
            if not df:
                continue
            inverse_frequency = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
            for document_index, terms in enumerate(counters):
                frequency = terms.get(token, 0)
                if frequency:
                    scores[query_index, document_index] += (
                        inverse_frequency * frequency * 2.2
                        / (
                            frequency
                            + 1.2 * (0.25 + 0.75 * lengths[document_index] / average_length)
                        )
                    )
    return scores


def sparse_matrix(
    queries: Sequence[Mapping[str, float]],
    documents: Sequence[Mapping[str, float]],
) -> np.ndarray:
    scores = np.zeros((len(queries), len(documents)), dtype=np.float32)
    inverted: dict[str, list[tuple[int, float]]] = {}
    for document_index, weights in enumerate(documents):
        for token, weight in weights.items():
            inverted.setdefault(str(token), []).append((document_index, float(weight)))
    for query_index, weights in enumerate(queries):
        for token, query_weight in weights.items():
            for document_index, document_weight in inverted.get(str(token), ()):
                scores[query_index, document_index] += float(query_weight) * document_weight
    return scores


def descending_ids(scores: np.ndarray, chunk_ids: Sequence[str]) -> tuple[str, ...]:
    order = sorted(range(len(chunk_ids)), key=lambda index: (-float(scores[index]), chunk_ids[index]))
    return tuple(chunk_ids[index] for index in order)


def fused_candidate_ids(
    dense_scores: np.ndarray,
    sparse_scores: np.ndarray,
    bm25_scores: np.ndarray,
    chunk_ids: Sequence[str],
    *,
    top_k: int,
) -> tuple[str, ...]:
    rankings = {
        "dense": descending_ids(dense_scores, chunk_ids),
        "sparse": descending_ids(sparse_scores, chunk_ids),
        "bm25": descending_ids(bm25_scores, chunk_ids),
    }
    return fuse_rankings(
        rankings,
        weights={"dense": 0.4, "sparse": 0.4, "bm25": 0.2},
        rrf_k=10,
        top_k=top_k,
    )


def colbert_scores(
    query_vectors: np.ndarray,
    document_vectors: Sequence[np.ndarray],
    document_indices: Sequence[int],
    *,
    device: str = "cuda:0",
) -> np.ndarray:
    """Compute BGE-M3 MaxSim scores from pre-encoded token vectors."""
    if not document_indices:
        return np.empty(0, dtype=np.float32)
    selected = [np.asarray(document_vectors[index]) for index in document_indices]
    max_length = max(len(item) for item in selected)
    dimension = int(np.asarray(query_vectors).shape[1])
    passages = torch.zeros(
        (len(selected), max_length, dimension), dtype=torch.float16, device=device,
    )
    mask = torch.zeros((len(selected), max_length), dtype=torch.bool, device=device)
    for index, vectors in enumerate(selected):
        length = len(vectors)
        passages[index, :length] = torch.as_tensor(
            vectors, dtype=torch.float16, device=device,
        )
        mask[index, :length] = True
    query = torch.as_tensor(query_vectors, dtype=torch.float16, device=device)
    similarities = torch.einsum("qd,cld->cql", query, passages)
    similarities = similarities.masked_fill(~mask[:, None, :], -torch.inf)
    return similarities.max(dim=2).values.mean(dim=1).float().cpu().numpy()


def evaluate_local_rankings(
    *, dataset, chunks: Sequence[LocalChunk], queries: Sequence[str],
    dense_scores: np.ndarray, sparse_scores: np.ndarray, bm25_scores: np.ndarray,
    query_colbert_vectors: Sequence[np.ndarray] | None = None,
    document_colbert_vectors: Sequence[np.ndarray] | None = None,
    cross_encoder_scores: np.ndarray | None = None,
    top_k: int = 50,
) -> dict[str, Any]:
    if len(dataset.cases) != len(queries):
        raise ValueError("query count must match dataset cases")
    chunk_ids = tuple(chunk.chunk_id for chunk in chunks)
    hit_by_id = {
        chunk.chunk_id: {
            "document_id": chunk.document_id,
            "source_start_char": chunk.start_char,
            "source_end_char": chunk.end_char,
        }
        for chunk in chunks
    }
    metric_rows: dict[str, list[dict[str, float]]] = {
        name: [] for name in (
            "bm25", "dense", "learned_sparse", "dense_bm25_rrf",
            "dense_sparse_rrf", "dense_sparse_bm25_rrf",
        )
    }
    if query_colbert_vectors is not None or document_colbert_vectors is not None:
        if query_colbert_vectors is None or document_colbert_vectors is None:
            raise ValueError("query and document ColBERT vectors must be supplied together")
        metric_rows.update({"colbert_rerank_top20": [], "all_modes_rerank_top20": []})
    if cross_encoder_scores is not None:
        metric_rows["cross_encoder_rerank_top20"] = []
    case_diagnostics = []
    started = time.perf_counter()
    for index, case in enumerate(dataset.cases):
        rankings = {
            "bm25": descending_ids(bm25_scores[index], chunk_ids),
            "dense": descending_ids(dense_scores[index], chunk_ids),
            "learned_sparse": descending_ids(sparse_scores[index], chunk_ids),
        }
        rankings["dense_bm25_rrf"] = fuse_rankings(
            {"dense": rankings["dense"], "bm25": rankings["bm25"]},
            weights={"dense": 0.25, "bm25": 0.75}, rrf_k=10, top_k=top_k,
        )
        rankings["dense_sparse_rrf"] = fuse_rankings(
            {"dense": rankings["dense"], "sparse": rankings["learned_sparse"]},
            weights={"dense": 0.5, "sparse": 0.5}, rrf_k=10, top_k=top_k,
        )
        rankings["dense_sparse_bm25_rrf"] = fused_candidate_ids(
            dense_scores[index], sparse_scores[index], bm25_scores[index],
            chunk_ids, top_k=top_k,
        )
        if cross_encoder_scores is not None:
            candidate_ids = rankings["dense_sparse_bm25_rrf"][:20]
            candidate_indices = [chunk_ids.index(chunk_id) for chunk_id in candidate_ids]
            rankings["cross_encoder_rerank_top20"] = tuple(sorted(
                candidate_ids,
                key=lambda chunk_id: (
                    -float(cross_encoder_scores[index, chunk_ids.index(chunk_id)]),
                    chunk_id,
                ),
            ))
        if query_colbert_vectors is not None and document_colbert_vectors is not None:
            candidate_ids = rankings["dense_sparse_bm25_rrf"][:20]
            candidate_indices = [chunk_ids.index(chunk_id) for chunk_id in candidate_ids]
            late_scores = colbert_scores(
                np.asarray(query_colbert_vectors[index]),
                document_colbert_vectors,
                candidate_indices,
            )
            positions = range(len(candidate_ids))
            rankings["colbert_rerank_top20"] = tuple(
                candidate_ids[position] for position in sorted(
                    positions,
                    key=lambda position: (-float(late_scores[position]), candidate_ids[position]),
                )
            )
            combined = [
                (
                    float(dense_scores[index, document_index])
                    + float(sparse_scores[index, document_index])
                    + float(late_scores[position])
                ) / 3.0
                for position, document_index in enumerate(candidate_indices)
            ]
            rankings["all_modes_rerank_top20"] = tuple(
                candidate_ids[position] for position in sorted(
                    positions,
                    key=lambda position: (-combined[position], candidate_ids[position]),
                )
            )
        for name, ranked_ids in rankings.items():
            metric_rows[name].append({
                f"recall@{cutoff}": evaluate_ranked_hits(
                    case, ranked_ids, hit_by_id, top_k=cutoff,
                )["evidence_recall"]
                for cutoff in (5, 20, 50)
            } | evaluate_ranked_hits(case, ranked_ids, hit_by_id, top_k=20))
        case_diagnostics.append({
            "case_id": case.case_id,
            "evidence_count": len(case.evidence),
            "evidence_document_ids": sorted({item.document_id for item in case.evidence}),
            "rankings": {
                name: _ranking_diagnostic(case, ranked_ids, hit_by_id)
                for name, ranked_ids in rankings.items()
                if name in {
                    "bm25", "dense", "learned_sparse",
                    "dense_bm25_rrf", "dense_sparse_bm25_rrf",
                    "cross_encoder_rerank_top20",
                }
            },
        })
    scoring_latency_ms = (time.perf_counter() - started) * 1000
    comparisons = {}
    if "cross_encoder_rerank_top20" in metric_rows:
        baseline = metric_rows["dense_sparse_bm25_rrf"]
        candidate = metric_rows["cross_encoder_rerank_top20"]
        comparisons["cross_encoder_vs_dense_sparse_bm25_rrf_at5"] = {
            "rescued": sum(
                left["recall@5"] < 1.0 and right["recall@5"] == 1.0
                for left, right in zip(baseline, candidate, strict=True)
            ),
            "harmed": sum(
                left["recall@5"] == 1.0 and right["recall@5"] < 1.0
                for left, right in zip(baseline, candidate, strict=True)
            ),
        }
    return {
        "case_count": len(dataset.cases),
        "chunk_count": len(chunks),
        "metrics": {
            name: {
                key: statistics.fmean(float(row[key]) for row in rows)
                for key in (
                    "recall@5", "recall@20", "recall@50", "document_recall",
                    "evidence_recall", "mrr", "ndcg",
                )
            } | {
                f"all_evidence_recall@{cutoff}": sum(
                    float(row[f"recall@{cutoff}"]) == 1.0 for row in rows
                ) / len(rows)
                for cutoff in (5, 20, 50)
            }
            for name, rows in metric_rows.items()
        },
        "ranking_and_metric_latency_ms": scoring_latency_ms,
        "comparisons": comparisons,
        "case_diagnostics": case_diagnostics,
    }


def _ranking_diagnostic(case, ranked_ids, hit_by_id) -> dict[str, Any]:
    def covers(hit, evidence) -> bool:
        return (
            str(hit["document_id"]) == evidence.document_id
            and (
                evidence.granularity == "document"
                or (
                    int(hit["source_start_char"]) <= evidence.start_char
                    and int(hit["source_end_char"]) >= evidence.end_char
                )
            )
        )

    evidence_ranks = []
    for evidence in case.evidence:
        evidence_ranks.append(next((
            rank for rank, chunk_id in enumerate(ranked_ids, 1)
            if chunk_id in hit_by_id and covers(hit_by_id[chunk_id], evidence)
        ), None))
    relevant_documents = {item.document_id for item in case.evidence}
    document_rank = next((
        rank for rank, chunk_id in enumerate(ranked_ids, 1)
        if chunk_id in hit_by_id
        and str(hit_by_id[chunk_id]["document_id"]) in relevant_documents
    ), None)
    return {
        "evidence_ranks": evidence_ranks,
        "all_evidence_rank": (
            max(evidence_ranks) if evidence_ranks and all(evidence_ranks) else None
        ),
        "first_gold_document_rank": document_rank,
    }
