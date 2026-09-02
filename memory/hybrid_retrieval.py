"""长期记忆的 BM25、向量与时间信号融合。

本模块只拥有候选排序，不访问 ChromaDB。存储适配器负责提供同一用户的
向量候选和可做关键词检索的语料；这里用 RRF 合并不同分值空间，避免把
向量距离、BM25 和时间戳直接做没有意义的数值相加。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Dict, Iterable, List, Sequence, Tuple

from application.chinese_lexical import tokenize_ascii_cjk_unigram_bigram
from application.memory_retrieval_policy import (
    DEFAULT_MEMORY_RETRIEVAL_POLICY,
    MemoryRetrievalPolicy,
)



@dataclass(frozen=True)
class MemoryDocument:
    """从持久层读取的一条原始情景记忆。"""

    memory_id: str
    content: str
    timestamp: str = ""
    conversation_id: str = ""
    summary: str = ""
    message_id: str = ""
    event_seq: int = 0
    role: str = ""
    chunk_index: int = 0


@dataclass(frozen=True)
class MemoryHit:
    """融合后的可解释检索结果。"""

    memory_id: str
    content: str
    score: float
    sources: Tuple[str, ...]
    timestamp: str = ""
    conversation_id: str = ""
    summary: str = ""
    message_id: str = ""
    event_seq: int = 0
    role: str = ""
    chunk_index: int = 0
    ranks: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        """生成 API/Trace 可安全投影的命中证据。"""
        return {
            "memory_id": self.memory_id,
            "score": round(self.score, 8),
            "sources": list(self.sources),
            "timestamp": self.timestamp,
            "conversation_id": self.conversation_id,
            "event_seq": self.event_seq,
            "role": self.role,
            "preview": self.content[:240],
            "ranks": dict(self.ranks),
        }


class HybridMemoryRetriever:
    """用 BM25 候选与向量候选构造有界 RRF 排名。"""

    def __init__(
        self,
        *,
        rrf_k: int = 60,
        vector_weight: float = 0.30,
        lexical_weight: float = 0.60,
        recency_weight: float = 0.10,
        lexical_pool: int = 20,
        policy: MemoryRetrievalPolicy | None = None,
    ):
        if policy is not None:
            rrf_k = policy.rrf_k
            vector_weight = policy.vector_weight
            lexical_weight = policy.lexical_weight
            recency_weight = policy.recency_weight
            lexical_pool = policy.lexical_pool
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        if lexical_pool < 1:
            raise ValueError("lexical_pool must be positive")
        weights = (vector_weight, lexical_weight, recency_weight)
        if any(weight < 0 for weight in weights) or sum(weights) <= 0:
            raise ValueError("retrieval weights must be non-negative and not all zero")
        self.rrf_k = int(rrf_k)
        self.vector_weight = float(vector_weight)
        self.lexical_weight = float(lexical_weight)
        self.recency_weight = float(recency_weight)
        self.lexical_pool = int(lexical_pool)
        self.policy = policy or (
            DEFAULT_MEMORY_RETRIEVAL_POLICY
            if (rrf_k, vector_weight, lexical_weight, recency_weight, lexical_pool)
            == (60, 0.30, 0.60, 0.10, 20)
            else MemoryRetrievalPolicy(
                version="memory-retrieval-policy-custom-v1",
                vector_weight=vector_weight, lexical_weight=lexical_weight,
                recency_weight=recency_weight, rrf_k=rrf_k,
                lexical_pool=lexical_pool,
            )
        )

    @property
    def policy_fingerprint(self) -> str:
        return self.policy.fingerprint

    def rank(
        self,
        query: str,
        *,
        vector_documents: Sequence[MemoryDocument],
        corpus_documents: Sequence[MemoryDocument],
        top_k: int = 5,
    ) -> List[MemoryHit]:
        """融合排名；空查询和无候选都确定性返回空列表。"""
        query = str(query or "").strip()
        if not query or top_k <= 0:
            return []

        documents = self._dedupe([*corpus_documents, *vector_documents])
        by_id = {document.memory_id: document for document in documents}
        if not by_id:
            return []

        vector_order = [
            document.memory_id
            for document in self._dedupe(vector_documents)
            if document.memory_id in by_id
        ]
        lexical_scores = self._bm25_scores(query, documents)
        lexical_order = [
            memory_id
            for memory_id, score in sorted(
                lexical_scores.items(), key=lambda item: (-item[1], item[0])
            )
            if score > 0
        ][: self.lexical_pool]

        # 时间只重排已经被语义或关键词召回的候选，不能独立引入无关记忆。
        candidate_ids = list(dict.fromkeys([*vector_order, *lexical_order]))
        if not candidate_ids:
            return []
        recency_order = sorted(
            candidate_ids,
            key=lambda memory_id: (
                -self._timestamp(by_id[memory_id].timestamp),
                memory_id,
            ),
        )

        rankings = {
            "vector": {memory_id: rank for rank, memory_id in enumerate(vector_order, 1)},
            "bm25": {memory_id: rank for rank, memory_id in enumerate(lexical_order, 1)},
            "recency": {memory_id: rank for rank, memory_id in enumerate(recency_order, 1)},
        }
        weights = {
            "vector": self.vector_weight,
            "bm25": self.lexical_weight,
            "recency": self.recency_weight,
        }

        hits: List[MemoryHit] = []
        for memory_id in candidate_ids:
            ranks = {
                source: ranking[memory_id]
                for source, ranking in rankings.items()
                if memory_id in ranking and weights[source] > 0
            }
            score = sum(
                weights[source] / (self.rrf_k + rank)
                for source, rank in ranks.items()
            )
            document = by_id[memory_id]
            hits.append(MemoryHit(
                memory_id=memory_id,
                content=document.content,
                score=score,
                sources=tuple(source for source in ("vector", "bm25", "recency") if source in ranks),
                timestamp=document.timestamp,
                conversation_id=document.conversation_id,
                summary=document.summary,
                message_id=document.message_id,
                event_seq=document.event_seq,
                role=document.role,
                chunk_index=document.chunk_index,
                ranks=ranks,
            ))
        return sorted(hits, key=lambda hit: (-hit.score, hit.memory_id))[:top_k]

    @classmethod
    def _bm25_scores(
        cls,
        query: str,
        documents: Sequence[MemoryDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> Dict[str, float]:
        """计算小型用户记忆语料的 BM25 分数。"""
        query_tokens = cls.tokenize(query)
        tokenized = {document.memory_id: cls.tokenize(document.content) for document in documents}
        if not query_tokens or not tokenized:
            return {document.memory_id: 0.0 for document in documents}
        avg_len = sum(len(tokens) for tokens in tokenized.values()) / max(1, len(tokenized))
        avg_len = max(1.0, avg_len)
        document_count = len(tokenized)
        frequencies: Dict[str, int] = {}
        for token in set(query_tokens):
            frequencies[token] = sum(token in tokens for tokens in tokenized.values())

        scores: Dict[str, float] = {}
        for memory_id, tokens in tokenized.items():
            score = 0.0
            length = max(1, len(tokens))
            for token in query_tokens:
                term_frequency = tokens.count(token)
                if term_frequency == 0:
                    continue
                document_frequency = frequencies.get(token, 0)
                inverse_frequency = math.log(
                    1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
                )
                denominator = term_frequency + k1 * (1 - b + b * length / avg_len)
                score += inverse_frequency * term_frequency * (k1 + 1) / denominator
            scores[memory_id] = score
        return scores

    @staticmethod
    def tokenize(text: str) -> List[str]:
        """同时保留英文/编号 token、中文单字和二元词，覆盖精确实体。"""
        return list(tokenize_ascii_cjk_unigram_bigram(text))

    @staticmethod
    def _dedupe(documents: Iterable[MemoryDocument]) -> List[MemoryDocument]:
        """按持久化 ID 去重并保留首次出现的排名。"""
        seen = set()
        result = []
        for document in documents:
            if document.memory_id in seen:
                continue
            seen.add(document.memory_id)
            result.append(document)
        return result

    @staticmethod
    def _timestamp(value: str) -> float:
        """把 ISO 时间转换为排序值；损坏时间作为最旧记录处理。"""
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0


@dataclass(frozen=True)
class RetrievalMetrics:
    """一条检索 case 的确定性指标。"""

    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float


def evaluate_retrieval(
    ranked_ids: Sequence[str],
    relevant_ids: Iterable[str],
    *,
    k: int,
) -> RetrievalMetrics:
    """计算 Recall@K、MRR 和二元相关性的 nDCG@K。"""
    relevant = set(relevant_ids)
    ranked = list(ranked_ids[: max(0, k)])
    if not relevant or k <= 0:
        return RetrievalMetrics(0.0, 0.0, 0.0)
    hits = [1 if memory_id in relevant else 0 for memory_id in ranked]
    recall = sum(hits) / len(relevant)
    reciprocal_rank = next((1.0 / rank for rank, hit in enumerate(hits, 1) if hit), 0.0)
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, 1))
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return RetrievalMetrics(
        recall_at_k=recall,
        reciprocal_rank=reciprocal_rank,
        ndcg_at_k=dcg / ideal_dcg if ideal_dcg else 0.0,
    )
