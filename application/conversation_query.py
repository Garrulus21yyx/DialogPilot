"""Public read models for transcript, invocation and projection progress."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from application.admission_contract import AdmissionStatus, ExecutionStatus
from application.delivery_contract import DeliveryStatusV1


@dataclass(frozen=True)
class TranscriptTurnView:
    seq: int
    role: str
    content: str
    created_at: str
    request_id: str | None


@dataclass(frozen=True)
class ProjectionProgressView:
    target_event_seq: int
    watermarks: Mapping[str, int]
    caught_up: bool


@dataclass(frozen=True)
class InvocationStatusView:
    invocation_key: str
    workflow_run_id: str
    admission_status: AdmissionStatus
    execution_status: ExecutionStatus | None
    runtime: Mapping[str, Any] | None
    pending_signal: Mapping[str, Any] | None
    final_response: Mapping[str, Any] | None
    delivery_status: DeliveryStatusV1 | None
    ticket: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ConversationCloseResult:
    close_id: str
    already_closed: bool
    closed_at: str
