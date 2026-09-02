"""Canonical-reference contracts for rebuildable retrieval projections."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence


class ProjectionContractError(ValueError):
    pass


class ProjectionEventStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"


class ProjectionResultCode(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    GENERATION_NOT_BUILDING = "GENERATION_NOT_BUILDING"
    CANONICAL_SOURCE_MISSING = "CANONICAL_SOURCE_MISSING"
    CANONICAL_SOURCE_DRIFT = "CANONICAL_SOURCE_DRIFT"
    SUBJECT_DELETION_FENCED = "SUBJECT_DELETION_FENCED"
    RESOLVER_UNAVAILABLE = "RESOLVER_UNAVAILABLE"


@dataclass(frozen=True)
class ProjectionEvent:
    event_id: str
    corpus: str
    tenant_id: str
    backend_id: str
    generation_id: str
    source_ref: str
    source_revision: str
    source_fingerprint: str
    subject_user_id: str | None
    subject_conversation_id: str | None
    deletion_epoch: int
    status: ProjectionEventStatus


@dataclass(frozen=True)
class ProjectionResult:
    event_id: str
    code: ProjectionResultCode
    candidate_count: int


class CanonicalProjectionResolver(Protocol):
    """Corpus owner port; implementations return only canonical projections."""

    corpus: str

    def project(self, connection, event: ProjectionEvent) -> Sequence[object]: ...
