"""Protocol-neutral chat input and outcome contracts shared by Target and adapters."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, TypeAlias


class StageStatus(str, Enum):
    OK = "ok"
    SKIPPED = "skipped"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class StageObservation:
    stage: str
    status: StageStatus
    detail: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True)
class ChatCommand:
    """Authenticated application input; protocol adapters must resolve identity."""

    message: str
    user_id: str
    tenant_id: str = "default"
    conv_id: Optional[str] = None
    request_id: Optional[str] = None
    continuation_id: Optional[str] = None
    pinned_bundle: Any = None
    authorization_fingerprint: str = ""
    asset_ids: tuple[str, ...] = ()
    approval_id: str | None = None
    approval_decision: bool | None = None
    interaction_id: str | None = None
    interaction_version: int | None = None
    interaction_values: tuple[tuple[str, str, object], ...] = ()


@dataclass(frozen=True)
class Completed:
    response_id: str
    response: Mapping[str, Any]
    stages: tuple[StageObservation, ...] = ()


@dataclass(frozen=True)
class Accepted:
    workflow_run_id: str
    public_status: Mapping[str, Any]


@dataclass(frozen=True)
class NeedsInput:
    workflow_run_id: str
    signal_id: str
    kind: str
    expires_at: str
    interaction_publication_id: str


@dataclass(frozen=True)
class HandedOff:
    ticket_id: str
    handoff_id: str


@dataclass(frozen=True)
class Cancelled:
    workflow_run_id: str
    reason_code: str


@dataclass(frozen=True)
class Expired:
    workflow_run_id: str
    stage: str
    new_request_required: bool = True


@dataclass(frozen=True)
class Reconciling:
    workflow_run_id: str
    public_status: Mapping[str, Any]
    next_poll_after: float


@dataclass(frozen=True)
class Rejected:
    code: str
    safe_message: str


@dataclass(frozen=True)
class Conflict:
    code: str
    existing_invocation: Mapping[str, Any]


@dataclass(frozen=True)
class Failed:
    code: str
    retryable: bool
    correlation_id: str
    safe_message: str = ""
    stages: tuple[StageObservation, ...] = ()


ChatOutcome: TypeAlias = (
    Completed
    | Accepted
    | NeedsInput
    | HandedOff
    | Cancelled
    | Expired
    | Reconciling
    | Rejected
    | Conflict
    | Failed
)


class ChatHandler(Protocol):
    async def handle(self, command: ChatCommand) -> ChatOutcome: ...
