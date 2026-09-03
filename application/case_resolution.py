"""Authenticated case-resolution facts and deterministic episode compilation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from application.data_location_registry import DataSubjectRef
from application.service_episode import (
    CaseOutcomeVerification,
    EpisodeEvidence,
    OutcomeVerificationStatus,
    ServiceEpisodeCandidate,
)


CASE_RESOLUTION_ACCEPTED = "CASE_RESOLUTION_ACCEPTED"
CASE_RESOLUTION_SCHEMA = "case-resolution-accepted-v1"
CASE_RESOLUTION_PROJECTOR = "case-resolution-episode-projector-v1"


class CaseResolutionError(RuntimeError):
    pass


class CaseResolutionConflict(CaseResolutionError):
    pass


class CaseOwnerMismatch(CaseResolutionError):
    pass


class CaseResolutionSourceUnavailable(CaseResolutionError):
    pass


@dataclass(frozen=True)
class AcceptCaseResolution:
    idempotency_key: str
    expected_ticket_version: int
    resolution: str
    authoritative_outcomes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip() or not self.resolution.strip():
            raise ValueError("case resolution command is incomplete")
        if self.expected_ticket_version < 1:
            raise ValueError("expected ticket version must be positive")
        if not self.authoritative_outcomes or any(
            not isinstance(item, str) or not item.strip()
            for item in self.authoritative_outcomes
        ):
            raise ValueError("case resolution requires authoritative outcomes")

    @property
    def fingerprint(self) -> str:
        return _sha256(
            {
                "idempotency_key": self.idempotency_key,
                "expected_ticket_version": self.expected_ticket_version,
                "resolution": self.resolution,
                "authoritative_outcomes": list(self.authoritative_outcomes),
            }
        )


@dataclass(frozen=True)
class AcceptedCaseResolution:
    verification_ref: str
    ticket_id: str
    revision: int
    subject: DataSubjectRef
    source_deletion_epoch: int
    source_invocation_key: str
    source_turn_refs: tuple[str, ...]
    problem: str
    resolution: str
    authoritative_outcomes: tuple[str, ...]
    case_owner: str
    accepted_at: str
    request_fingerprint: str
    schema_version: str = CASE_RESOLUTION_SCHEMA

    def __post_init__(self) -> None:
        required = (
            self.verification_ref,
            self.ticket_id,
            self.subject.tenant_id,
            self.subject.user_id,
            self.subject.conversation_id,
            self.source_invocation_key,
            self.problem,
            self.resolution,
            self.case_owner,
            self.accepted_at,
            self.request_fingerprint,
        )
        if any(not str(item).strip() for item in required):
            raise ValueError("accepted case resolution is incomplete")
        if self.revision < 1 or self.source_deletion_epoch < 0:
            raise ValueError("accepted case resolution version is invalid")
        if not self.source_turn_refs or len(self.source_turn_refs) != len(
            set(self.source_turn_refs)
        ):
            raise ValueError("accepted case resolution source refs are invalid")
        if not self.authoritative_outcomes:
            raise ValueError("accepted case resolution requires outcomes")
        if len(self.request_fingerprint) != 64:
            raise ValueError("accepted case resolution fingerprint is invalid")
        if self.schema_version != CASE_RESOLUTION_SCHEMA:
            raise ValueError("accepted case resolution schema is unsupported")

    def to_event_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "ticket_id": self.ticket_id,
            "revision": self.revision,
            "tenant_id": self.subject.tenant_id,
            "user_id": self.subject.user_id,
            "conversation_id": self.subject.conversation_id,
            "source_deletion_epoch": self.source_deletion_epoch,
            "source_invocation_key": self.source_invocation_key,
            "source_turn_refs": list(self.source_turn_refs),
            "problem": self.problem,
            "resolution": self.resolution,
            "authoritative_outcomes": list(self.authoritative_outcomes),
            "case_owner": self.case_owner,
            "accepted_at": self.accepted_at,
            "request_fingerprint": self.request_fingerprint,
        }

    @classmethod
    def from_event(
        cls,
        verification_ref: str,
        payload: Mapping[str, Any],
    ) -> "AcceptedCaseResolution":
        return cls(
            verification_ref=verification_ref,
            ticket_id=str(payload["ticket_id"]),
            revision=int(payload["revision"]),
            subject=DataSubjectRef(
                str(payload["tenant_id"]),
                str(payload["user_id"]),
                str(payload["conversation_id"]),
            ),
            source_deletion_epoch=int(payload["source_deletion_epoch"]),
            source_invocation_key=str(payload["source_invocation_key"]),
            source_turn_refs=tuple(map(str, payload["source_turn_refs"])),
            problem=str(payload["problem"]),
            resolution=str(payload["resolution"]),
            authoritative_outcomes=tuple(
                map(
                    str,
                    payload["authoritative_outcomes"],
                )
            ),
            case_owner=str(payload["case_owner"]),
            accepted_at=str(payload["accepted_at"]),
            request_fingerprint=str(payload["request_fingerprint"]),
            schema_version=str(payload["schema_version"]),
        )


@dataclass(frozen=True)
class CaseResolutionSourceTurn:
    turn_ref: str
    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in {"inbound", "assistant", "human"}:
            raise ValueError("case resolution source role is unsupported")
        if not self.turn_ref.strip() or not self.content.strip():
            raise ValueError("case resolution source turn is incomplete")


def compile_service_episode(
    accepted: AcceptedCaseResolution,
    source_turns: tuple[CaseResolutionSourceTurn, ...],
) -> ServiceEpisodeCandidate:
    """Compile one accepted owner fact without inference or database access."""
    if tuple(item.turn_ref for item in source_turns) != accepted.source_turn_refs:
        raise CaseResolutionSourceUnavailable(
            "accepted source turns do not match the canonical refs"
        )
    evidence = tuple(
        EpisodeEvidence(
            source_event_ref=item.turn_ref,
            role="user" if item.role == "inbound" else "assistant",
            content=item.content,
        )
        for item in source_turns
    )
    return ServiceEpisodeCandidate(
        subject=accepted.subject,
        source_deletion_epoch=accepted.source_deletion_epoch,
        case_id=accepted.ticket_id,
        case_status="resolved",
        revision=accepted.revision,
        expected_version=accepted.revision - 1,
        problem=accepted.problem,
        product_version="",
        symptoms=(),
        materials=(),
        actions=(),
        authoritative_outcomes=accepted.authoritative_outcomes,
        resolution=accepted.resolution,
        root_cause="",
        outcome_verification=CaseOutcomeVerification(
            verification_ref=accepted.verification_ref,
            status=OutcomeVerificationStatus.ACCEPTED,
            case_owner=accepted.case_owner,
            verified_at=accepted.accepted_at,
        ),
        evidence=evidence,
        extractor_version=CASE_RESOLUTION_PROJECTOR,
    )


def _sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
