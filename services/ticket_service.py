"""Handoff 工单领域合同；持久事实由 PostgreSQL 实现拥有。"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol

from application.case_resolution import AcceptCaseResolution, AcceptedCaseResolution
from core.auth import Principal


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


LEGAL_TRANSITIONS = {
    TicketStatus.OPEN: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
    TicketStatus.IN_PROGRESS: {
        TicketStatus.WAITING_CUSTOMER, TicketStatus.RESOLVED, TicketStatus.CLOSED,
    },
    TicketStatus.WAITING_CUSTOMER: {
        TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED, TicketStatus.CLOSED,
    },
    TicketStatus.RESOLVED: {TicketStatus.IN_PROGRESS, TicketStatus.CLOSED},
    TicketStatus.CLOSED: set(),
}


class TicketError(Exception):
    """工单领域有类型失败的基类。"""


class TicketNotFoundError(TicketError):
    """目标工单不存在。"""


class InvalidTransitionError(TicketError):
    def __init__(self, current: TicketStatus, target: TicketStatus):
        super().__init__(f"illegal ticket transition: {current.value} -> {target.value}")
        self.current = current
        self.target = target


class IdempotencyConflictError(TicketError):
    """同一幂等键被用于不同的稳定请求内容。"""


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    idempotency_key: str
    user_id: str
    conv_id: str
    request_id: str
    question: str
    published_response: str
    reason: str
    priority: TicketPriority
    status: TicketStatus
    agent_type: str
    intent: str
    verification_status: str
    assignee: Optional[str]
    created_at: str
    updated_at: str
    version: int
    identity_metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["priority"] = self.priority.value
        data["status"] = self.status.value
        return data


@dataclass(frozen=True)
class TicketOutboxMessage:
    event_id: str
    event_type: str
    ticket_id: str
    payload: dict[str, Any]
    attempts: int
    created_at: str


class TicketService(Protocol):
    """API/Agent 消费的工单端口；唯一实现是 PostgreSQL owner。"""

    def create_ticket(self, **kwargs: Any) -> tuple[Ticket, bool]: ...
    def get_ticket(self, ticket_id: str) -> Ticket: ...
    def get_ticket_by_idempotency_key(self, **kwargs: Any) -> Ticket: ...
    def get_ticket_view(self, ticket_id: str) -> dict[str, Any]: ...
    def list_tickets(self, **kwargs: Any) -> list[Ticket]: ...
    def list_active_tickets(self, **kwargs: Any) -> list[Ticket]: ...
    def accept_resolution(
        self,
        ticket_id: str,
        command: AcceptCaseResolution,
        *,
        principal: Principal,
    ) -> tuple[AcceptedCaseResolution, bool]: ...


class TicketWebhookDispatcher:
    """将 outbox 事件投递到外部 CRM；接收端按 event_id 幂等。"""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0):
        self._url = str(url or "").strip()
        if not self._url:
            raise ValueError("ticket dispatch webhook URL must not be blank")
        self._timeout_seconds = max(0.1, float(timeout_seconds))

    def __call__(self, message: TicketOutboxMessage) -> None:
        body = json.dumps({
            "event_id": message.event_id,
            "event_type": message.event_type,
            "ticket_id": message.ticket_id,
            "payload": message.payload,
            "created_at": message.created_at,
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        request = urllib.request.Request(
            self._url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": message.event_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
            if not 200 <= int(response.status) < 300:
                raise RuntimeError(f"ticket webhook returned HTTP {response.status}")
