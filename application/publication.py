"""Unified publication commands; each kind preserves its own terminal meaning."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, TypeAlias

from application.delivery_contract import ConnectorCapability, DeliveryStatusV1
from application.work_item import WorkControlBinding
from core.identity import InvocationKey


class PublicationKind(str, Enum):
    FINAL_RESPONSE = "final_response"
    INTERACTION_REQUEST = "interaction_request"
    HUMAN_REPLY = "human_reply"


class PublicationApplyStatus(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


class ProjectionDisposition(str, Enum):
    NORMAL = "normal"
    OUT_OF_SCOPE = "out_of_scope"
    CLARIFICATION = "clarification"
    APPROVAL = "approval"


@dataclass(frozen=True)
class PublicationPolicy:
    connector_capability: ConnectorCapability
    max_attempts: int
    retry_policy_version: str
    reconcile_deadline: str
    next_attempt_at: str | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if not self.retry_policy_version or not self.reconcile_deadline:
            raise ValueError("retry policy and reconcile deadline are required")


@dataclass(frozen=True)
class FinalResponseCommand:
    invocation_key: InvocationKey
    tenant_id: str
    user_id: str
    conversation_id: str
    response_text: str
    candidate_id: str
    producer: str
    verifier_status: str
    verification: Mapping[str, Any]
    evidence_sha256: str
    bundle_version: str
    index_manifest_sha256: str
    created_at: str
    policy: PublicationPolicy
    projection_disposition: ProjectionDisposition = ProjectionDisposition.NORMAL
    public_response: Mapping[str, Any] = field(default_factory=dict)
    execution_stages: tuple[Mapping[str, Any], ...] = ()
    expected_work_controls: tuple[WorkControlBinding, ...] = ()


@dataclass(frozen=True)
class InteractionRequestCommand:
    invocation_key: InvocationKey
    tenant_id: str
    user_id: str
    conversation_id: str
    signal_id: str
    signal_version: int
    challenge: str
    resume_schema: Mapping[str, Any]
    created_at: str
    policy: PublicationPolicy
    projection_disposition: ProjectionDisposition = ProjectionDisposition.APPROVAL
    expected_work_controls: tuple[WorkControlBinding, ...] = ()
    related_signals: tuple[tuple[str, int], ...] = ()
    execution_stages: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class HumanReplyCommand:
    tenant_id: str
    user_id: str
    conversation_id: str
    ticket_id: str
    handoff_id: str
    human_message_id: str
    text: str
    created_at: str
    policy: PublicationPolicy
    projection_disposition: ProjectionDisposition = ProjectionDisposition.NORMAL


PublicationCommand: TypeAlias = (
    FinalResponseCommand | InteractionRequestCommand | HumanReplyCommand
)


@dataclass(frozen=True)
class PublicationRecord:
    publication_id: str
    kind: PublicationKind
    delivery_operation_key: str
    tenant_id: str
    user_id: str
    conversation_id: str
    seq: int
    content_sha256: str
    status: DeliveryStatusV1
    outbound_turn_key: str
    outbound_event_id: str
    delivery_outbox_id: str
    invocation_key: InvocationKey | None


@dataclass(frozen=True)
class PublicationResult:
    status: PublicationApplyStatus
    record: PublicationRecord


def publication_identity(command: PublicationCommand) -> tuple[str, str, str]:
    if isinstance(command, FinalResponseCommand):
        subject = f"invocation:{command.invocation_key}"
    elif isinstance(command, InteractionRequestCommand):
        subject = f"signal:{command.signal_id}:v{command.signal_version}"
    else:
        subject = (
            f"human:{command.ticket_id}:{command.handoff_id}:"
            f"{command.human_message_id}"
        )
    publication_id = _stable("publication", subject)
    operation_key = _stable("delivery-operation", publication_id)
    outbox_id = _stable("delivery-outbox", operation_key)
    return publication_id, operation_key, outbox_id


def command_fingerprint(command: PublicationCommand) -> str:
    raw = dict(command.__dict__)
    # Retry timing is transport metadata, not part of the selected publication fact.
    raw.pop("created_at", None)
    if not raw.get("related_signals"):
        raw.pop("related_signals", None)
    # Older interaction publications have no diagnostic field. An absent
    # observation and an empty observation set describe the same publication.
    if isinstance(command, InteractionRequestCommand) and not command.execution_stages:
        raw.pop("execution_stages", None)
    raw["policy"] = dict(command.policy.__dict__)
    raw["invocation_key"] = str(raw.get("invocation_key") or "")
    raw["expected_work_controls"] = [
        dict(item.__dict__) for item in raw.get("expected_work_controls", ())
    ]
    encoded = json.dumps(
        raw, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable(namespace: str, subject: str) -> str:
    encoded = json.dumps(
        ["dialogpilot.publication.v1", namespace, subject],
        separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return f"{namespace}:v1:{hashlib.sha256(encoded).hexdigest()}"
