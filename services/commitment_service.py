"""客服承诺的领域合同；承诺只能由显式人工或业务动作创建。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class CommitmentStatus(str, Enum):
    SCHEDULED = "scheduled"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    BREACHED = "breached"
    LATE_FULFILLED = "late_fulfilled"
    ESCALATED = "escalated"
    ARCHIVED = "archived"


LEGAL_COMMITMENT_TRANSITIONS = {
    CommitmentStatus.SCHEDULED: {
        CommitmentStatus.FULFILLED,
        CommitmentStatus.CANCELLED,
        CommitmentStatus.BREACHED,
    },
    CommitmentStatus.BREACHED: {
        CommitmentStatus.LATE_FULFILLED,
        CommitmentStatus.ESCALATED,
    },
    CommitmentStatus.ESCALATED: {CommitmentStatus.LATE_FULFILLED},
    CommitmentStatus.FULFILLED: {CommitmentStatus.ARCHIVED},
    CommitmentStatus.LATE_FULFILLED: {CommitmentStatus.ARCHIVED},
    CommitmentStatus.CANCELLED: {CommitmentStatus.ARCHIVED},
    CommitmentStatus.ARCHIVED: set(),
}


class CommitmentError(Exception):
    """承诺领域有类型失败的基类。"""


class CommitmentNotFoundError(CommitmentError):
    pass


class CommitmentIdempotencyConflictError(CommitmentError):
    pass


class CommitmentVersionConflictError(CommitmentError):
    pass


class InvalidCommitmentTransitionError(CommitmentError):
    def __init__(self, current: CommitmentStatus, target: CommitmentStatus):
        super().__init__(
            f"illegal commitment transition: {current.value} -> {target.value}"
        )
        self.current = current
        self.target = target


@dataclass(frozen=True)
class Commitment:
    commitment_id: str
    idempotency_key: str
    user_id: str
    conversation_id: str
    ticket_id: str | None
    kind: str
    description: str
    due_at: str
    owner: str
    source_kind: str
    source_receipt_ref: str | None
    status: CommitmentStatus
    version: int
    retention_class: str
    breached_at: str | None
    escalated_at: str | None
    fulfilled_at: str | None
    archived_at: str | None
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result
