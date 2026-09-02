"""Inbound-first commands and typed dispositions for M1 admission."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol, TypeAlias

from core.identity import InvocationIdentity, OperationKey


class ResumeRejectionCode(str, Enum):
    UNAUTHORIZED = "UNAUTHORIZED"
    EXPIRED = "EXPIRED"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    KIND_MISMATCH = "KIND_MISMATCH"
    SCHEMA_MISMATCH = "SCHEMA_MISMATCH"
    NOT_FOUND = "NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


@dataclass(frozen=True)
class NewInvocationInbound:
    identity: InvocationIdentity
    message: str
    pinned_versions: Mapping[str, str]
    created_at: str
    retention_until: str | None = None


@dataclass(frozen=True)
class ExplicitResumeBinding:
    signal_id: str
    expected_version: int
    kind: str
    reply_to_publication_id: str
    schema_fingerprint: str


@dataclass(frozen=True)
class ResumeTarget:
    signal_id: str
    expected_version: int
    workflow_run_id: str
    kind: str
    schema_fingerprint: str


@dataclass(frozen=True)
class ValidResumeInbound:
    identity: InvocationIdentity
    message: str
    binding: ExplicitResumeBinding
    target: ResumeTarget
    created_at: str
    retention_until: str | None = None


@dataclass(frozen=True)
class InvalidResumeInbound:
    identity: InvocationIdentity
    message: str
    binding: ExplicitResumeBinding
    code: ResumeRejectionCode
    created_at: str
    retention_until: str | None = None


InboundDisposition: TypeAlias = (
    NewInvocationInbound | ValidResumeInbound | InvalidResumeInbound
)


@dataclass(frozen=True)
class ResumeQueued:
    outbox_id: OperationKey
    workflow_run_id: str
    signal_id: str


@dataclass(frozen=True)
class ResumeRejected:
    code: ResumeRejectionCode
    event_id: str


ResumeAdmissionResult: TypeAlias = ResumeQueued | ResumeRejected


class SignalAuthority(Protocol):
    def validate(
        self,
        binding: ExplicitResumeBinding,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
    ) -> ResumeTarget | ResumeRejectionCode: ...


class InboundDispositionResolver:
    """No-LLM binding validator executed before any Memory/RAG/model/tool work."""

    def __init__(self, signal_authority: SignalAuthority):
        self.signal_authority = signal_authority

    def resolve(
        self,
        *,
        identity: InvocationIdentity,
        message: str,
        pinned_versions: Mapping[str, str],
        created_at: str,
        binding: ExplicitResumeBinding | None = None,
        retention_until: str | None = None,
    ) -> InboundDisposition:
        if binding is None:
            return NewInvocationInbound(
                identity, message, dict(pinned_versions), created_at, retention_until,
            )
        result = self.signal_authority.validate(
            binding,
            tenant_id=str(identity.tenant_id),
            user_id=str(identity.user_id),
            conversation_id=str(identity.conversation_id),
        )
        if isinstance(result, ResumeRejectionCode):
            return InvalidResumeInbound(
                identity, message, binding, result, created_at, retention_until,
            )
        return ValidResumeInbound(
            identity, message, binding, result, created_at, retention_until,
        )
