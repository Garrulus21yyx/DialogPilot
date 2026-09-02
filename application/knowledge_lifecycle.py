"""Knowledge-owned review lifecycle and publication-pointer contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class KnowledgeLifecycleError(ValueError):
    """Typed fail-closed lifecycle or publication error."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


class SourceRevisionStatus(str, Enum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    REJECTED = "REJECTED"
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    RETRACTED = "RETRACTED"


LEGAL_TRANSITIONS = frozenset({
    (SourceRevisionStatus.DRAFT, SourceRevisionStatus.REVIEWED),
    (SourceRevisionStatus.DRAFT, SourceRevisionStatus.REJECTED),
    (SourceRevisionStatus.REVIEWED, SourceRevisionStatus.STAGED),
    (SourceRevisionStatus.STAGED, SourceRevisionStatus.ACTIVE),
    (SourceRevisionStatus.STAGED, SourceRevisionStatus.REJECTED),
    (SourceRevisionStatus.ACTIVE, SourceRevisionStatus.SUPERSEDED),
    (SourceRevisionStatus.ACTIVE, SourceRevisionStatus.RETRACTED),
})


def validate_transition(
    current: SourceRevisionStatus | str,
    target: SourceRevisionStatus | str,
) -> tuple[SourceRevisionStatus, SourceRevisionStatus]:
    try:
        pair = (SourceRevisionStatus(current), SourceRevisionStatus(target))
    except ValueError as exc:
        raise KnowledgeLifecycleError(
            "INVALID_TRANSITION", "unknown source revision state",
        ) from exc
    if pair not in LEGAL_TRANSITIONS:
        raise KnowledgeLifecycleError(
            "INVALID_TRANSITION", f"illegal transition {pair[0].value}->{pair[1].value}",
        )
    return pair


@dataclass(frozen=True)
class ReviewDecision:
    reviewer_id: str
    reason_code: str
    evidence_ref: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if not all((self.reviewer_id.strip(), self.reason_code.strip(), self.evidence_ref.strip())):
            raise KnowledgeLifecycleError("INVALID_REVIEW", "review provenance is required")
        if self.decided_at.tzinfo is None:
            raise KnowledgeLifecycleError("INVALID_REVIEW", "review time must be timezone-aware")


@dataclass(frozen=True)
class GateApproval:
    gate_id: str
    decision: str
    evidence_sha256: str
    approver_id: str

    def __post_init__(self) -> None:
        if not all((self.gate_id.strip(), self.decision.strip(), self.approver_id.strip())):
            raise KnowledgeLifecycleError("GATE_NOT_READY", "gate approval is incomplete")
        if len(self.evidence_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.evidence_sha256
        ):
            raise KnowledgeLifecycleError("GATE_NOT_READY", "gate evidence hash is invalid")

    def require(self, expected_gate_id: str) -> None:
        if self.gate_id != expected_gate_id or self.decision != "READY":
            raise KnowledgeLifecycleError(
                "GATE_NOT_READY", f"{expected_gate_id} READY approval is required",
            )


class PublicationPointerKind(str, Enum):
    SCOPED_CANARY = "SCOPED_CANARY"
    GLOBAL = "GLOBAL"

    @property
    def required_gate_id(self) -> str:
        return (
            "M5-KNOWLEDGE-GATE"
            if self is PublicationPointerKind.SCOPED_CANARY
            else "KNOWLEDGE_LIFECYCLE_GA"
        )


@dataclass(frozen=True)
class PublicationScope:
    tenant_id: str
    backend_id: str
    pointer_kind: PublicationPointerKind
    scope: str
    locale: str
    product: str
    region: str

    def __post_init__(self) -> None:
        required = (self.tenant_id, self.backend_id, self.scope, self.locale, self.region)
        if any(not value.strip() for value in required):
            raise KnowledgeLifecycleError("INVALID_PUBLICATION_SCOPE", "publication scope is incomplete")
        if self.pointer_kind is PublicationPointerKind.GLOBAL and (
            self.scope != "*" or self.locale != "*" or self.product or self.region != "*"
        ):
            raise KnowledgeLifecycleError(
                "INVALID_PUBLICATION_SCOPE", "global pointer must use the canonical wildcard scope",
            )

    @property
    def key(self) -> tuple[str, ...]:
        return (
            self.tenant_id, self.backend_id, self.pointer_kind.value,
            self.scope, self.locale, self.product, self.region,
        )


@dataclass(frozen=True)
class PinnedKnowledgeManifest:
    request_id: str
    generation_id: str
    manifest_hash: str
    pointer_version: int
    publication_scope: PublicationScope

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.generation_id.strip():
            raise KnowledgeLifecycleError("INVALID_MANIFEST_PIN", "request and generation are required")
        if self.pointer_version < 1 or len(self.manifest_hash) != 64:
            raise KnowledgeLifecycleError("INVALID_MANIFEST_PIN", "manifest pin is invalid")


class KnowledgeCandidateKind(str, Enum):
    NEGATIVE_FEEDBACK = "NEGATIVE_FEEDBACK"
    SUPPORT_TICKET = "SUPPORT_TICKET"
    ZERO_HIT = "ZERO_HIT"


@dataclass(frozen=True)
class KnowledgeCandidate:
    candidate_id: str
    tenant_id: str
    kind: KnowledgeCandidateKind
    sanitized_payload: Mapping[str, Any]
    source_ref: str
    privacy_review_ref: str
    created_at: datetime
    schema_version: str = "knowledge-candidate-v1"

    def __post_init__(self) -> None:
        if not all((
            self.candidate_id.strip(), self.tenant_id.strip(),
            self.source_ref.strip(), self.privacy_review_ref.strip(),
        )):
            raise KnowledgeLifecycleError("INVALID_CANDIDATE", "candidate provenance is required")
        if self.created_at.tzinfo is None or not self.sanitized_payload:
            raise KnowledgeLifecycleError("INVALID_CANDIDATE", "candidate payload/time is invalid")

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(json.dumps(
            self.sanitized_payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
