"""Projection policy and deletion-fence contracts for conversation events."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from application.case_resolution import CASE_RESOLUTION_ACCEPTED


class ProjectionName(str, Enum):
    WORKING_WINDOW = "working_window"
    THREAD_SUMMARY = "thread_summary"
    FACT_EXTRACTION = "fact_extraction"


class ProjectionApplyStatus(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"


class ProjectionOutboxOutcome(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"
    POLICY_SKIPPED = "POLICY_SKIPPED"
    DELETION_FENCED = "DELETION_FENCED"


@dataclass(frozen=True)
class ConversationSubject:
    tenant_id: str
    user_id: str
    conversation_id: str


@dataclass(frozen=True)
class ProjectableConversationEvent:
    outbox_id: str
    projection_name: ProjectionName
    generation: int
    event_id: str
    event_seq: int
    event_type: str
    payload: Mapping[str, Any]
    source_deletion_epoch: int
    subject: ConversationSubject
    attempt: int

    @property
    def operation_key(self) -> str:
        return (
            f"{self.projection_name.value}:g{self.generation}:"
            f"{self.event_id}:e{self.source_deletion_epoch}"
        )


@dataclass(frozen=True)
class SubjectFence:
    deletion_epoch: int
    deleted: bool


class ConversationProjectionPolicyV1:
    version = "conversation-projection-v1"

    _FACT_TRIGGER_EVENTS = {
        "FINAL_RESPONSE_SELECTED",
        "LEGACY_FINAL_RESPONSE_IMPORTED",
    }

    _CONTEXT_ONLY_EVENTS = {
        "INTERACTION_REQUEST_PUBLISHED",
        "RESUME_REJECTED",
    }

    _NON_TURN_EVENTS = {CASE_RESOLUTION_ACCEPTED}

    def allows(self, event: ProjectableConversationEvent) -> bool:
        if event.event_type == "CONVERSATION_DELETED":
            return True
        if event.event_type in self._NON_TURN_EVENTS:
            return False
        if (
            event.projection_name is ProjectionName.FACT_EXTRACTION
            and event.event_type not in self._FACT_TRIGGER_EVENTS
        ):
            return False
        if event.projection_name in {
            ProjectionName.WORKING_WINDOW,
            ProjectionName.THREAD_SUMMARY,
        }:
            return True
        if event.event_type in self._CONTEXT_ONLY_EVENTS:
            return False
        disposition = str(event.payload.get("projection_disposition") or "normal")
        if disposition in {"out_of_scope", "clarification", "approval"}:
            return False
        return True


class ConversationProjectionAdapter(Protocol):
    def apply(
        self, event: ProjectableConversationEvent,
    ) -> ProjectionApplyStatus: ...

    def delete_subject(
        self,
        subject: ConversationSubject,
        *,
        through_deletion_epoch: int,
    ) -> None: ...


@dataclass(frozen=True)
class ProjectionDispatchResult:
    outbox_id: str
    status: str
    error_type: str = ""
