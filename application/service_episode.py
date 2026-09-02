"""Canonical ServiceEpisode lifecycle and persistence contract."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum

from application.data_location_registry import DataSubjectRef


class ServiceEpisodeError(RuntimeError):
    pass


class ServiceEpisodeConflict(ServiceEpisodeError):
    pass


class OutcomeVerificationStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CaseOutcomeVerification:
    verification_ref: str
    status: OutcomeVerificationStatus
    case_owner: str
    verified_at: str

    def __post_init__(self) -> None:
        if any(not str(item).strip() for item in (
            self.verification_ref, self.case_owner, self.verified_at,
        )):
            raise ValueError("case outcome verification is incomplete")
        try:
            datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("case outcome verification time is invalid") from exc


@dataclass(frozen=True)
class EpisodeEvidence:
    source_event_ref: str
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"user", "assistant"}:
            raise ValueError("episode evidence role is unsupported")
        if not self.source_event_ref.strip() or not self.content.strip():
            raise ValueError("episode evidence requires source and content")


@dataclass(frozen=True)
class ServiceEpisodeCandidate:
    subject: DataSubjectRef
    source_deletion_epoch: int
    case_id: str
    case_status: str
    revision: int
    expected_version: int
    problem: str
    product_version: str
    symptoms: tuple[str, ...]
    materials: tuple[str, ...]
    actions: tuple[str, ...]
    authoritative_outcomes: tuple[str, ...]
    resolution: str
    root_cause: str
    outcome_verification: CaseOutcomeVerification
    evidence: tuple[EpisodeEvidence, ...]
    extractor_version: str
    entity_ids: tuple[str, ...] = ()
    schema_version: str = "service-episode-v1"

    def __post_init__(self) -> None:
        if self.case_status not in {"resolved", "closed"}:
            raise ValueError("only resolved/closed cases can form service episodes")
        if self.outcome_verification.status is not OutcomeVerificationStatus.ACCEPTED:
            raise ValueError("case owner must accept the authoritative outcome")
        if self.revision < 1 or self.expected_version < 0:
            raise ValueError("invalid service episode revision")
        if self.source_deletion_epoch < 0:
            raise ValueError("invalid service episode deletion epoch")
        if any(not str(item).strip() for item in (
            self.case_id, self.problem, self.resolution, self.extractor_version,
        )):
            raise ValueError("service episode required fields are incomplete")
        if not self.authoritative_outcomes:
            raise ValueError("service episode requires authoritative outcomes")
        if not self.evidence:
            raise ValueError("service episode requires explicit source evidence")
        refs = [item.source_event_ref for item in self.evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("service episode source event refs must be unique")
        if self.schema_version != "service-episode-v1":
            raise ValueError("unsupported service episode schema")

    @property
    def episode_id(self) -> str:
        return self.case_id

    @property
    def user_evidence(self) -> tuple[EpisodeEvidence, ...]:
        return tuple(item for item in self.evidence if item.role == "user")

    @property
    def assistant_evidence(self) -> tuple[EpisodeEvidence, ...]:
        return tuple(item for item in self.evidence if item.role == "assistant")

    @property
    def source_event_refs(self) -> tuple[str, ...]:
        return tuple(item.source_event_ref for item in self.evidence)

    @property
    def provenance_sha256(self) -> str:
        payload = {
            "subject": asdict(self.subject),
            "source_deletion_epoch": self.source_deletion_epoch,
            "case_id": self.case_id,
            "case_status": self.case_status,
            "revision": self.revision,
            "problem": self.problem,
            "product_version": self.product_version,
            "symptoms": list(self.symptoms),
            "materials": list(self.materials),
            "actions": list(self.actions),
            "authoritative_outcomes": list(self.authoritative_outcomes),
            "resolution": self.resolution,
            "root_cause": self.root_cause,
            "outcome_verification": {
                **asdict(self.outcome_verification),
                "status": self.outcome_verification.status.value,
            },
            "evidence": [asdict(item) for item in self.evidence],
            "extractor_version": self.extractor_version,
            "entity_ids": list(self.entity_ids),
            "schema_version": self.schema_version,
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EpisodeProjectionTarget:
    backend_id: str
    generation_id: str

    def __post_init__(self) -> None:
        if not self.backend_id.strip() or not self.generation_id.strip():
            raise ValueError("episode projection target is incomplete")


@dataclass(frozen=True)
class ServiceEpisodeCommit:
    episode_id: str
    revision: int
    expected_version: int
    projection_event_id: str | None
    already_applied: bool


def evidence_json(items: tuple[EpisodeEvidence, ...]) -> list[dict[str, str]]:
    return [asdict(item) for item in items]
