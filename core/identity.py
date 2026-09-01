"""Stable, protocol-neutral identities for one customer-service invocation."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol, TypeAlias


class IdentityContractError(ValueError):
    """An identity is blank, malformed, or outside its authoritative scope."""


class ContinuationFrameNotFound(IdentityContractError):
    pass


class ContinuationFrameConflict(IdentityContractError):
    pass


class _Identifier(str):
    max_length = 200

    def __new__(cls, value: object):
        normalized = str(value or "").strip()
        if not normalized:
            raise IdentityContractError(f"{cls.__name__} must not be blank")
        if len(normalized) > cls.max_length:
            raise IdentityContractError(
                f"{cls.__name__} exceeds {cls.max_length} characters"
            )
        return str.__new__(cls, normalized)


class TenantId(_Identifier):
    pass


class UserId(_Identifier):
    pass


class ConversationId(_Identifier):
    pass


class ContinuationId(_Identifier):
    pass


class RequestId(_Identifier):
    max_length = 128


class TurnId(_Identifier):
    pass


class WorkflowRunId(_Identifier):
    pass


class TurnKey(_Identifier):
    @classmethod
    def build(
        cls,
        tenant_id: TenantId,
        user_id: UserId,
        conversation_id: ConversationId,
        request_id: RequestId,
    ) -> "TurnKey":
        return cls(_stable_key(
            "turn",
            tenant_id,
            user_id,
            conversation_id,
            request_id,
        ))


class InvocationKey(_Identifier):
    @classmethod
    def build(
        cls,
        tenant_id: TenantId,
        user_id: UserId,
        conversation_id: ConversationId,
        request_id: RequestId,
    ) -> "InvocationKey":
        return cls(_stable_key(
            "invocation",
            tenant_id,
            user_id,
            conversation_id,
            request_id,
        ))


class OperationKey(_Identifier):
    @classmethod
    def build(
        cls,
        invocation_key: InvocationKey,
        *,
        owner: str,
        operation: str,
        subject: str,
    ) -> "OperationKey":
        return cls(_stable_key(
            "operation",
            invocation_key,
            _required_component(owner, "owner"),
            _required_component(operation, "operation"),
            _required_component(subject, "subject"),
        ))


def _stable_key(namespace: str, *components: object) -> str:
    """Hash a versioned JSON tuple so separators inside IDs cannot collide."""
    payload = json.dumps(
        ["dialogpilot.identity.v1", namespace, *map(str, components)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{namespace}:v1:{hashlib.sha256(payload).hexdigest()}"


def _required_component(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise IdentityContractError(f"{field} must not be blank")
    return normalized


@dataclass(frozen=True)
class InvocationIdentity:
    tenant_id: TenantId
    user_id: UserId
    conversation_id: ConversationId
    request_id: RequestId
    continuation_id: ContinuationId
    turn_id: TurnId
    workflow_run_id: WorkflowRunId

    @property
    def turn_key(self) -> TurnKey:
        return TurnKey.build(
            self.tenant_id, self.user_id, self.conversation_id, self.request_id,
        )

    @property
    def invocation_key(self) -> InvocationKey:
        return InvocationKey.build(
            self.tenant_id, self.user_id, self.conversation_id, self.request_id,
        )

    def operation_key(self, owner: str, operation: str, subject: str) -> OperationKey:
        return OperationKey.build(
            self.invocation_key,
            owner=owner,
            operation=operation,
            subject=subject,
        )

    def metadata(self) -> dict[str, str]:
        return {
            "tenant_id": str(self.tenant_id),
            "user_id": str(self.user_id),
            "conversation_id": str(self.conversation_id),
            "request_id": str(self.request_id),
            "continuation_id": str(self.continuation_id),
            "turn_id": str(self.turn_id),
            "workflow_run_id": str(self.workflow_run_id),
            "turn_key": str(self.turn_key),
            "invocation_key": str(self.invocation_key),
        }


class IdentityFactory:
    """The sole request-boundary generator for opaque invocation identities."""

    def __init__(self, new_opaque: Callable[[], str] | None = None):
        self._new_opaque = new_opaque or (lambda: uuid.uuid4().hex)

    def create_invocation(
        self,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: str | None,
        request_id: str | None,
        continuation_id: str | None = None,
    ) -> InvocationIdentity:
        resolved_tenant = TenantId(tenant_id)
        resolved_user = UserId(user_id)
        resolved_conversation = ConversationId(conversation_id or self._new_opaque())
        resolved_request = RequestId(request_id or self._new_opaque())
        invocation_key = InvocationKey.build(
            resolved_tenant,
            resolved_user,
            resolved_conversation,
            resolved_request,
        )
        return InvocationIdentity(
            tenant_id=resolved_tenant,
            user_id=resolved_user,
            conversation_id=resolved_conversation,
            request_id=resolved_request,
            continuation_id=ContinuationId(
                continuation_id or _stable_key("continuation", invocation_key)
            ),
            turn_id=TurnId(_stable_key("turn-id", invocation_key)),
            workflow_run_id=WorkflowRunId(_stable_key("workflow-run", invocation_key)),
        )


@dataclass(frozen=True)
class StartNew:
    pass


@dataclass(frozen=True)
class ReusePriorFrame:
    frame_ref: str
    frame_version: int

    def __post_init__(self) -> None:
        _required_component(self.frame_ref, "frame_ref")
        if self.frame_version < 1:
            raise IdentityContractError("frame_version must be positive")


ContinuationTarget: TypeAlias = StartNew | ReusePriorFrame


@dataclass(frozen=True)
class ContinuationFrame:
    frame_ref: str
    frame_version: int
    tenant_id: TenantId
    user_id: UserId
    conversation_id: ConversationId
    continuation_id: ContinuationId


class ContinuationFrameReader(Protocol):
    def get(self, frame_ref: str) -> ContinuationFrame | None: ...


class ContinuationIdFactory:
    """Resolve only an Agent-gated target; never accepts a model-minted ID."""

    def __init__(
        self,
        frame_reader: ContinuationFrameReader,
        new_opaque: Callable[[], str] | None = None,
    ):
        self._frame_reader = frame_reader
        self._new_opaque = new_opaque or (lambda: uuid.uuid4().hex)

    def resolve(
        self,
        target: ContinuationTarget,
        *,
        tenant_id: TenantId,
        user_id: UserId,
        conversation_id: ConversationId,
    ) -> ContinuationId:
        if isinstance(target, StartNew):
            return ContinuationId(self._new_opaque())
        if not isinstance(target, ReusePriorFrame):
            raise IdentityContractError("unsupported continuation target")
        frame = self._frame_reader.get(target.frame_ref)
        if frame is None:
            raise ContinuationFrameNotFound(target.frame_ref)
        expected = (
            target.frame_version,
            tenant_id,
            user_id,
            conversation_id,
        )
        actual = (
            frame.frame_version,
            frame.tenant_id,
            frame.user_id,
            frame.conversation_id,
        )
        if actual != expected:
            raise ContinuationFrameConflict(
                "continuation frame version or principal scope changed"
            )
        return frame.continuation_id
