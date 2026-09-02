"""M4-T02 fixed-range ThreadSummary contracts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from application.conversation_projection import (
    ConversationSubject,
    ProjectableConversationEvent,
    ProjectionApplyStatus,
)


class ThreadSummaryConflict(RuntimeError):
    pass


class ThreadSummaryApplyStatus(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_APPLIED = "ALREADY_APPLIED"


class ThreadSummaryProjectionStatus(str, Enum):
    APPLIED = "APPLIED"
    ALREADY_CURRENT = "ALREADY_CURRENT"
    POLICY_SKIPPED = "POLICY_SKIPPED"
    DEGRADED = "DEGRADED"


class ThreadSummaryState(str, Enum):
    READY = "READY"
    LAGGING = "LAGGING"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class SummarySourceItem:
    seq: int
    event_id: str
    event_type: str
    content_sha256: str
    role: str = ""
    content: str = ""


@dataclass(frozen=True)
class ThreadSummaryJob:
    job_id: str
    subject: ConversationSubject
    generation: int
    from_seq: int
    to_seq: int
    source_watermark: int
    source_sha256: str
    source_deletion_epoch: int
    expected_version: int
    items: tuple[SummarySourceItem, ...]
    included_ranges: tuple[tuple[int, int], ...]
    omitted_ranges: tuple[tuple[int, int], ...]
    pending_message_count: int
    pending_token_estimate: int
    summarizer_version: str
    schema_version: str = "thread-summary-v1"

    def __post_init__(self) -> None:
        if self.generation < 1 or self.from_seq < 1 or self.to_seq < self.from_seq:
            raise ValueError("thread summary range is invalid")
        if len(self.source_sha256) != 64 or not self.items:
            raise ValueError("thread summary source is incomplete")
        if self.pending_message_count < 0 or self.pending_token_estimate < 0:
            raise ValueError("thread summary pending metrics are invalid")
        if tuple(item.seq for item in self.items) != tuple(
            range(self.from_seq, self.to_seq + 1)
        ):
            raise ValueError("thread summary source range is not contiguous")


@dataclass(frozen=True)
class ThreadSummaryView:
    state: ThreadSummaryState
    generation: int
    source_watermark: int
    projection_watermark: int
    version: int
    summaries: tuple[str, ...]
    included_ranges: tuple[Mapping, ...]
    omitted_ranges: tuple[Mapping, ...]
    conflicts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.state, ThreadSummaryState):
            object.__setattr__(self, "state", ThreadSummaryState(self.state))
        if self.projection_watermark > self.source_watermark:
            raise ValueError("thread summary projection exceeds its source")
        if self.state is ThreadSummaryState.READY and (
            self.projection_watermark != self.source_watermark or self.conflicts
        ):
            raise ValueError("READY thread summary must cover its source")


@dataclass(frozen=True)
class ThreadSummaryPolicy:
    version: str = "thread-summary-policy-v1"
    message_threshold: int = 12
    token_threshold: int = 2_000
    max_events_per_job: int = 50

    _EXPLICIT_EVENTS = frozenset({
        "CONVERSATION_FINALIZED", "HANDOFF_REQUESTED",
    })

    def __post_init__(self) -> None:
        if self.message_threshold < 1 or self.token_threshold < 1:
            raise ValueError("thread summary thresholds must be positive")
        if self.max_events_per_job < 1 or self.max_events_per_job > 500:
            raise ValueError("thread summary job bound must be between 1 and 500")

    def allows(
        self,
        job: ThreadSummaryJob,
        *,
        event: ProjectableConversationEvent | None = None,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        if event is not None and (
            event.event_type in self._EXPLICIT_EVENTS
            or event.payload.get("thread_idle") is True
        ):
            return True
        return (
            job.pending_message_count >= self.message_threshold
            or job.pending_token_estimate >= self.token_threshold
        )


class ThreadSummaryRepository(Protocol):
    def prepare(
        self, subject: ConversationSubject, *, summarizer_version: str,
        max_events: int = 50,
    ) -> ThreadSummaryJob | None: ...

    def commit(
        self, job: ThreadSummaryJob, *, summary: str,
    ) -> ThreadSummaryApplyStatus: ...

    def mark_degraded(self, job: ThreadSummaryJob, *, error_code: str) -> None: ...

    def read(self, subject: ConversationSubject) -> ThreadSummaryView: ...

    def start_rebuild(self, subject: ConversationSubject) -> int: ...


class ThreadSummarizer(Protocol):
    version: str

    def summarize(self, items: tuple[SummarySourceItem, ...]) -> str: ...


class ThreadSummaryProjector:
    """The sole range/checkpoint owner; model adapters only produce candidates."""

    def __init__(
        self,
        repository: ThreadSummaryRepository,
        summarizer: ThreadSummarizer,
        *,
        policy: ThreadSummaryPolicy | None = None,
    ):
        if not summarizer.version.strip():
            raise ValueError("summarizer version is required")
        self.repository = repository
        self.summarizer = summarizer
        self.policy = policy or ThreadSummaryPolicy()

    def project_subject(
        self,
        subject: ConversationSubject,
        *,
        event: ProjectableConversationEvent | None = None,
        force: bool = False,
    ) -> ThreadSummaryProjectionStatus:
        job = self.repository.prepare(
            subject, summarizer_version=self.summarizer.version,
            max_events=self.policy.max_events_per_job,
        )
        if job is None:
            return ThreadSummaryProjectionStatus.ALREADY_CURRENT
        if not self.policy.allows(job, event=event, force=force):
            return ThreadSummaryProjectionStatus.POLICY_SKIPPED
        try:
            candidate = self.summarizer.summarize(job.items)
            status = self.repository.commit(job, summary=candidate)
        except ThreadSummaryConflict:
            raise
        except Exception as exc:
            self.repository.mark_degraded(
                job, error_code=f"{type(exc).__name__}:model_unavailable",
            )
            return ThreadSummaryProjectionStatus.DEGRADED
        return (
            ThreadSummaryProjectionStatus.APPLIED
            if status is ThreadSummaryApplyStatus.APPLIED
            else ThreadSummaryProjectionStatus.ALREADY_CURRENT
        )

    def apply(
        self, event: ProjectableConversationEvent,
    ) -> ProjectionApplyStatus:
        status = self.project_subject(event.subject, event=event)
        return (
            ProjectionApplyStatus.APPLIED
            if status in {
                ThreadSummaryProjectionStatus.APPLIED,
                ThreadSummaryProjectionStatus.DEGRADED,
            }
            else ProjectionApplyStatus.ALREADY_APPLIED
        )

    def delete_subject(
        self, subject: ConversationSubject, *, through_deletion_epoch: int,
    ) -> None:
        # PostgreSQL conversation deletion owns the atomic purge trigger.
        return None

    def rebuild(self, subject: ConversationSubject) -> ThreadSummaryView:
        self.repository.start_rebuild(subject)
        while self.project_subject(subject, force=True) is (
            ThreadSummaryProjectionStatus.APPLIED
        ):
            pass
        return self.repository.read(subject)

    def repair_if_corrupt(self, subject: ConversationSubject) -> ThreadSummaryView:
        view = self.repository.read(subject)
        return (
            self.rebuild(subject)
            if view.state is ThreadSummaryState.DEGRADED else view
        )
