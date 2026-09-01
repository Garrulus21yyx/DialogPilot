"""可从 Chroma 权威 chunk 重建的持久 BM25 倒排索引。"""

from __future__ import annotations

import math
import pathlib
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from memory.hybrid_retrieval import HybridMemoryRetriever


@dataclass(frozen=True)
class SparseDocument:
    chunk_id: str
    text: str


class PersistentBM25Index:
    """SQLite posting-list projection; corpus content remains authoritative elsewhere."""

    SCHEMA_VERSION = 1
    TOKENIZER_VERSION = "ascii-cjk-unigram-bigram-v1"

    def __init__(self, path: str, *, k1: float = 1.5, b: float = 0.75):
        self.path = str(path)
        self.k1 = float(k1)
        self.b = float(b)
        if self.path != ":memory:":
            pathlib.Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS sparse_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sparse_documents (
                chunk_id TEXT PRIMARY KEY,
                token_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sparse_postings (
                term TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                term_frequency INTEGER NOT NULL,
                PRIMARY KEY (term, chunk_id),
                FOREIGN KEY (chunk_id) REFERENCES sparse_documents(chunk_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_sparse_postings_term
                ON sparse_postings(term);
        """)

    def ensure(self, documents: Sequence[SparseDocument], *, corpus_fingerprint: str) -> bool:
        """确保投影对应同一语料；返回本次是否发生重建。"""
        with self._lock:
            metadata = self._metadata()
            count = self._connection.execute("SELECT COUNT(*) FROM sparse_documents").fetchone()[0]
            compatible = {
                "schema_version": str(self.SCHEMA_VERSION),
                "tokenizer_version": self.TOKENIZER_VERSION,
                "corpus_fingerprint": str(corpus_fingerprint),
                "document_count": str(len(documents)),
                "k1": str(self.k1),
                "b": str(self.b),
            }
            if (
                all(metadata.get(key) == value for key, value in compatible.items())
                and count == len(documents)
            ):
                return False
            self.rebuild(documents, corpus_fingerprint=corpus_fingerprint)
            return True

    def rebuild(self, documents: Sequence[SparseDocument], *, corpus_fingerprint: str) -> None:
        rows = []
        postings = []
        for document in documents:
            tokens = HybridMemoryRetriever.tokenize(document.text)
            rows.append((str(document.chunk_id), len(tokens)))
            postings.extend(
                (term, str(document.chunk_id), frequency)
                for term, frequency in Counter(tokens).items()
            )
        metadata = {
            "schema_version": str(self.SCHEMA_VERSION),
            "tokenizer_version": self.TOKENIZER_VERSION,
            "corpus_fingerprint": str(corpus_fingerprint),
            "document_count": str(len(rows)),
            "built_at": datetime.now(timezone.utc).isoformat(),
            "k1": str(self.k1),
            "b": str(self.b),
        }
        with self._lock, self._connection:
            self._connection.execute("DELETE FROM sparse_postings")
            self._connection.execute("DELETE FROM sparse_documents")
            self._connection.execute("DELETE FROM sparse_metadata")
            self._connection.executemany(
                "INSERT INTO sparse_documents(chunk_id, token_count) VALUES (?, ?)", rows,
            )
            self._connection.executemany(
                "INSERT INTO sparse_postings(term, chunk_id, term_frequency) VALUES (?, ?, ?)",
                postings,
            )
            self._connection.executemany(
                "INSERT INTO sparse_metadata(key, value) VALUES (?, ?)", metadata.items(),
            )

    def search(self, query: str, *, top_k: int) -> list[str]:
        query_tokens = HybridMemoryRetriever.tokenize(query)
        if not query_tokens or top_k < 1:
            return []
        terms = tuple(dict.fromkeys(query_tokens))
        placeholders = ",".join("?" for _ in terms)
        with self._lock:
            stats = self._connection.execute(
                "SELECT COUNT(*), COALESCE(AVG(token_count), 0) FROM sparse_documents"
            ).fetchone()
            document_count, average_length = int(stats[0]), max(1.0, float(stats[1]))
            if document_count == 0:
                return []
            posting_rows = self._connection.execute(
                f"SELECT term, chunk_id, term_frequency FROM sparse_postings WHERE term IN ({placeholders})",
                terms,
            ).fetchall()
            lengths = dict(self._connection.execute(
                "SELECT chunk_id, token_count FROM sparse_documents WHERE chunk_id IN "
                f"(SELECT chunk_id FROM sparse_postings WHERE term IN ({placeholders}))",
                terms,
            ).fetchall())
        by_term: dict[str, dict[str, int]] = {term: {} for term in terms}
        for term, chunk_id, frequency in posting_rows:
            by_term[str(term)][str(chunk_id)] = int(frequency)
        document_frequency = {term: len(rows) for term, rows in by_term.items()}
        scores: dict[str, float] = {}
        for term in query_tokens:
            frequency = document_frequency.get(term, 0)
            inverse = math.log(1 + (document_count - frequency + 0.5) / (frequency + 0.5))
            for chunk_id, term_frequency in by_term.get(term, {}).items():
                length = max(1, int(lengths.get(chunk_id, 0)))
                denominator = term_frequency + self.k1 * (
                    1 - self.b + self.b * length / average_length
                )
                scores[chunk_id] = scores.get(chunk_id, 0.0) + (
                    inverse * term_frequency * (self.k1 + 1) / denominator
                )
        return [
            chunk_id for chunk_id, score in sorted(
                scores.items(), key=lambda item: (-item[1], item[0]),
            ) if score > 0
        ][:top_k]

    @property
    def manifest(self) -> dict[str, object]:
        with self._lock:
            metadata = self._metadata()
        return {
            **metadata,
            "engine": "sqlite-bm25",
            "schema_version": self.SCHEMA_VERSION,
            "tokenizer_version": self.TOKENIZER_VERSION,
            "k1": self.k1,
            "b": self.b,
        }

    def _metadata(self) -> dict[str, str]:
        return dict(self._connection.execute("SELECT key, value FROM sparse_metadata").fetchall())

    def close(self) -> None:
        with self._lock:
            self._connection.close()
