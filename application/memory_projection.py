"""Closed M4-T01 read contract for projected conversation Memory."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class MemoryProjectionState(str, Enum):
    READY = "READY"
    LAGGING = "LAGGING"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class MemoryRetrievalOutcome(str, Enum):
    NOT_NEEDED = "NOT_NEEDED"
    NO_MATCH = "NO_MATCH"
    HITS = "HITS"
    UNAVAILABLE = "UNAVAILABLE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class ProjectionRange:
    projection: str
    from_seq: int
    to_seq: int
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.projection or self.from_seq < 1 or self.to_seq < self.from_seq:
            raise ValueError("memory projection range is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection": self.projection,
            "from_seq": self.from_seq,
            "to_seq": self.to_seq,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MemoryProjectionResult:
    state: MemoryProjectionState
    context: Any
    source_watermark: int
    projection_watermarks: Mapping[str, int]
    retrieval_outcome: MemoryRetrievalOutcome
    included_ranges: tuple[ProjectionRange, ...] = ()
    omitted_ranges: tuple[ProjectionRange, ...] = ()
    conflicts: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    raw_fallback_used: bool = False

    def __post_init__(self) -> None:
        if self.source_watermark < 0:
            raise ValueError("source watermark must be non-negative")
        if any(int(value) < 0 for value in self.projection_watermarks.values()):
            raise ValueError("projection watermark must be non-negative")
        if self.state is MemoryProjectionState.READY:
            if self.conflicts or self.omitted_ranges or self.raw_fallback_used:
                raise ValueError("READY memory projection cannot omit or conflict")
            if any(
                int(value) < self.source_watermark
                for value in self.projection_watermarks.values()
            ):
                raise ValueError("READY memory projection must cover source watermark")
        if self.state is MemoryProjectionState.UNAVAILABLE and not self.reason_codes:
            raise ValueError("UNAVAILABLE memory projection requires a reason")
