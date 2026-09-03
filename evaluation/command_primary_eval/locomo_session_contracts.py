"""Evaluation-only contracts for session-level conversational retrieval."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BenchmarkSessionDocument:
    conversation_id: str
    session_id: str
    occurred_at: str
    content: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.conversation_id,
                self.session_id,
                self.occurred_at,
                self.content,
            )
        ):
            raise ValueError("benchmark session document is incomplete")
        if not self.source_refs or any(not item.strip() for item in self.source_refs):
            raise ValueError("benchmark session source refs are incomplete")


@dataclass(frozen=True)
class BenchmarkMemoryCase:
    question_id: str
    conversation_id: str
    category: int
    question: str
    gold_session_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (
                self.question_id,
                self.conversation_id,
                self.question,
            )
        ):
            raise ValueError("benchmark memory case is incomplete")
        if not self.gold_session_ids:
            raise ValueError("benchmark memory case requires gold sessions")


@dataclass(frozen=True)
class RankedSessionHit:
    session_id: str
    score: float

    def __post_init__(self) -> None:
        if not self.session_id.strip() or not math.isfinite(self.score):
            raise ValueError("ranked session hit is invalid")


class SessionCandidateRetriever(Protocol):
    version: str

    @property
    def descriptor(self) -> Mapping[str, object]: ...

    def retrieve(
        self,
        *,
        query: str,
        documents: Sequence[BenchmarkSessionDocument],
        top_k: int,
    ) -> Sequence[RankedSessionHit]: ...


@dataclass(frozen=True)
class LocomoSessionSlice:
    dataset_id: str
    source_revision: str
    source_sha256: str
    sample_id: str
    category: int
    documents: tuple[BenchmarkSessionDocument, ...]
    cases: tuple[BenchmarkMemoryCase, ...]
