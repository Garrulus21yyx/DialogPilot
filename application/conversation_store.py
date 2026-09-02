"""Protocol-neutral immutable conversation fact contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from core.identity import (
    ConversationId,
    InvocationKey,
    OperationKey,
    RequestId,
    TenantId,
    TurnId,
    TurnKey,
    UserId,
)


class ConversationStoreError(RuntimeError):
    pass


class ConversationAccessDenied(ConversationStoreError):
    pass


class ConversationNotFound(ConversationStoreError):
    pass


class AppendStatus(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


class TurnRole(str, Enum):
    INBOUND = "inbound"
    ASSISTANT = "assistant"
    HUMAN = "human"
    SYSTEM_EVENT = "system_event"


@dataclass(frozen=True)
class ConversationScope:
    tenant_id: TenantId
    user_id: UserId
    conversation_id: ConversationId


@dataclass(frozen=True)
class TurnToAppend:
    turn_key: TurnKey
    turn_id: TurnId
    role: TurnRole
    content: str
    created_at: str
    request_id: RequestId | None = None
    invocation_key: InvocationKey | None = None
    metadata: Mapping[str, Any] | None = None
    retention_until: str | None = None

    @property
    def content_sha256(self) -> str:
        return content_hash({"role": self.role.value, "content": self.content})


@dataclass(frozen=True)
class ConversationTurn:
    turn_key: TurnKey
    turn_id: TurnId
    scope: ConversationScope
    seq: int
    role: TurnRole
    content: str
    content_sha256: str
    request_id: RequestId | None
    invocation_key: InvocationKey | None
    metadata: Mapping[str, Any]
    created_at: str


@dataclass(frozen=True)
class EventToAppend:
    event_id: str
    operation_key: OperationKey
    event_type: str
    payload: Mapping[str, Any]
    created_at: str
    request_id: RequestId | None = None
    invocation_key: InvocationKey | None = None
    retention_until: str | None = None

    @property
    def content_sha256(self) -> str:
        return content_hash({"event_type": self.event_type, "payload": self.payload})


@dataclass(frozen=True)
class AppendTurnResult:
    status: AppendStatus
    turn: ConversationTurn | None


class ConversationTurnStore(Protocol):
    def append_turn(
        self, scope: ConversationScope, turn: TurnToAppend,
    ) -> AppendTurnResult: ...

    def list_turns(
        self, scope: ConversationScope, *, after_seq: int = 0, limit: int = 100,
    ) -> tuple[ConversationTurn, ...]: ...

    def append_event(
        self, scope: ConversationScope, event: EventToAppend,
    ) -> AppendStatus: ...


def content_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
