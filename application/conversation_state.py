"""Authoritative conversation control state and optimistic transition algebra."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from threading import RLock
from typing import Protocol

from application.agent_result import ReceiptRef, RequestedField
from application.work_item import ArgumentValue, WorkItem
from core.identity import ConversationId, TenantId, UserId


class ConversationStateError(ValueError):
    pass


class ConversationStateConflict(ConversationStateError):
    pass


class WorkstreamStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WAITING_INPUT = "WAITING_INPUT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    PAUSED = "PAUSED"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ConversationOwner(str, Enum):
    AUTOMATION = "AUTOMATION"
    HUMAN = "HUMAN"


@dataclass(frozen=True)
class WorkstreamState:
    workstream_id: str
    owner_agent: str
    capability_ref: str
    phase: str
    status: WorkstreamStatus
    state_version: int
    slots: tuple[ArgumentValue, ...] = ()
    flow_ref: str | None = None

    def __post_init__(self) -> None:
        _required(
            self.workstream_id,
            self.owner_agent,
            self.capability_ref,
            self.phase,
        )
        if self.state_version < 1:
            raise ConversationStateError("workstream version must be positive")
        _unique((item.name for item in self.slots), "workstream slots")
        if self.flow_ref is not None and not self.flow_ref.strip():
            raise ConversationStateError("flow_ref must be absent or nonblank")

    @property
    def terminal(self) -> bool:
        return self.status in {WorkstreamStatus.COMPLETED, WorkstreamStatus.CANCELLED}

    def with_slots(
        self,
        values: tuple[ArgumentValue, ...],
        *,
        status: WorkstreamStatus = WorkstreamStatus.ACTIVE,
    ) -> "WorkstreamState":
        slots = {item.name: item for item in self.slots}
        slots.update({item.name: item for item in values})
        return replace(
            self,
            slots=tuple(slots[name] for name in sorted(slots)),
            status=status,
            state_version=self.state_version + 1,
        )

    def transition(self, status: WorkstreamStatus, *, phase: str | None = None) -> "WorkstreamState":
        if self.terminal:
            if status is self.status and (phase is None or phase == self.phase):
                return self
            raise ConversationStateConflict("terminal workstream cannot transition")
        return replace(
            self,
            status=status,
            phase=phase or self.phase,
            state_version=self.state_version + 1,
        )


@dataclass(frozen=True)
class PendingInteractionState:
    interaction_id: str
    version: int
    requested_fields: tuple[RequestedField, ...]
    workstream_versions: tuple[tuple[str, int], ...]
    suspended_work_items: tuple[WorkItem, ...] = ()
    checkpoint_thread_id: str | None = None

    def __post_init__(self) -> None:
        _required(self.interaction_id)
        if self.version < 1 or not self.requested_fields:
            raise ConversationStateError("pending interaction identity is incomplete")
        identities = tuple(
            (item.target_work_item_id, item.field_name)
            for item in self.requested_fields
        )
        _unique(identities, "pending requested fields")
        _unique((item[0] for item in self.workstream_versions), "pending workstreams")
        if any(version < 1 for _, version in self.workstream_versions):
            raise ConversationStateError("pending workstream version must be positive")
        suspended_ids = tuple(item.work_item_id for item in self.suspended_work_items)
        _unique(suspended_ids, "suspended work items")
        requested_targets = {item.target_work_item_id for item in self.requested_fields}
        if self.suspended_work_items and requested_targets.difference(suspended_ids):
            raise ConversationStateError(
                "requested field target must be a suspended work item"
            )
        if any(item.effect.value != "READ" for item in self.suspended_work_items):
            raise ConversationStateError("user-input suspension supports read work only")
        if self.checkpoint_thread_id is not None and not self.checkpoint_thread_id.strip():
            raise ConversationStateError("checkpoint thread ID must be absent or nonblank")


@dataclass(frozen=True)
class PendingApprovalState:
    approval_id: str
    version: int
    workstream_id: str
    work_item_id: str
    action_ref: str
    operation_key: str
    target_entity_ref: str
    target_entity_version: str
    expires_at: str
    arguments: tuple[ArgumentValue, ...] = ()
    checkpoint_thread_id: str | None = None

    def __post_init__(self) -> None:
        _required(
            self.approval_id,
            self.workstream_id,
            self.work_item_id,
            self.action_ref,
            self.operation_key,
            self.target_entity_ref,
            self.target_entity_version,
            self.expires_at,
        )
        if self.version < 1:
            raise ConversationStateError("approval version must be positive")
        _unique((item.name for item in self.arguments), "approval arguments")
        if self.checkpoint_thread_id is not None and not self.checkpoint_thread_id.strip():
            raise ConversationStateError("checkpoint thread ID must be absent or nonblank")
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError as exc:
            raise ConversationStateError("approval expiry is invalid") from exc
        if expires.utcoffset() is None:
            raise ConversationStateError("approval expiry must be timezone-aware")


@dataclass(frozen=True)
class AcceptedApprovalState:
    approval_id: str
    version: int
    workstream_id: str
    action_ref: str
    operation_key: str
    target_entity_ref: str
    target_entity_version: str
    arguments: tuple[ArgumentValue, ...]

    def __post_init__(self) -> None:
        _required(
            self.approval_id,
            self.workstream_id,
            self.action_ref,
            self.operation_key,
            self.target_entity_ref,
            self.target_entity_version,
        )
        if self.version < 1:
            raise ConversationStateError("accepted approval version must be positive")
        _unique((item.name for item in self.arguments), "accepted approval arguments")


@dataclass(frozen=True)
class ResumeBinding:
    token: str
    workstream_id: str
    workstream_version: int

    def __post_init__(self) -> None:
        _required(self.token, self.workstream_id)
        if self.workstream_version < 1:
            raise ConversationStateError("resume workstream version must be positive")


@dataclass(frozen=True)
class ConversationState:
    tenant_id: TenantId
    user_id: UserId
    conversation_id: ConversationId
    version: int
    workstreams: tuple[WorkstreamState, ...] = ()
    pending_interaction: PendingInteractionState | None = None
    pending_approval: PendingApprovalState | None = None
    resume_bindings: tuple[ResumeBinding, ...] = ()
    consumed_signal_ids: tuple[str, ...] = ()
    schema_version: str = "conversation-state-v2"
    owner: ConversationOwner = ConversationOwner.AUTOMATION
    human_ticket_ref: str | None = None
    accepted_approvals: tuple[AcceptedApprovalState, ...] = ()

    def __post_init__(self) -> None:
        if self.version < 0:
            raise ConversationStateError("conversation version must be non-negative")
        _required(self.schema_version)
        if self.owner is ConversationOwner.HUMAN and not str(
            self.human_ticket_ref or ""
        ).strip():
            raise ConversationStateError("human-owned conversation requires ticket receipt")
        if self.owner is ConversationOwner.AUTOMATION and self.human_ticket_ref is not None:
            raise ConversationStateError("automation cannot claim a human ticket")
        _unique((item.workstream_id for item in self.workstreams), "workstreams")
        _unique((item.token for item in self.resume_bindings), "resume tokens")
        _unique(self.consumed_signal_ids, "consumed signals")
        _unique(
            ((item.approval_id, item.version) for item in self.accepted_approvals),
            "accepted approvals",
        )
        by_id = {item.workstream_id: item for item in self.workstreams}
        if any(
            item.workstream_id not in by_id for item in self.accepted_approvals
        ):
            raise ConversationStateError("accepted approval references unknown workstream")
        if self.pending_interaction is not None:
            expected = dict(self.pending_interaction.workstream_versions)
            if set(expected).difference(by_id):
                raise ConversationStateError("interaction references unknown workstream")
            if any(by_id[item].state_version != version for item, version in expected.items()):
                raise ConversationStateError("interaction workstream version is stale")
        if self.pending_approval is not None:
            stream = by_id.get(self.pending_approval.workstream_id)
            if stream is None or stream.status is not WorkstreamStatus.WAITING_APPROVAL:
                raise ConversationStateError("approval is not bound to a waiting workstream")
        if any(
            binding.workstream_id not in by_id
            or by_id[binding.workstream_id].state_version != binding.workstream_version
            for binding in self.resume_bindings
        ):
            raise ConversationStateError("resume binding is stale or unknown")

    @classmethod
    def empty(
        cls,
        *,
        tenant_id: str,
        user_id: str,
        conversation_id: str,
    ) -> "ConversationState":
        return cls(TenantId(tenant_id), UserId(user_id), ConversationId(conversation_id), 0)

    @property
    def active_workstreams(self) -> tuple[WorkstreamState, ...]:
        return tuple(item for item in self.workstreams if not item.terminal)

    @property
    def fingerprint(self) -> str:
        payload = {
            "scope": (str(self.tenant_id), str(self.user_id), str(self.conversation_id)),
            "version": self.version,
            "workstreams": [
                {
                    "id": item.workstream_id,
                    "owner": item.owner_agent,
                    "capability": item.capability_ref,
                    "flow": item.flow_ref,
                    "phase": item.phase,
                    "status": item.status.value,
                    "version": item.state_version,
                    "slots": [(slot.name, slot.value_json) for slot in item.slots],
                }
                for item in self.workstreams
            ],
            "pending_interaction": (
                self.pending_interaction.interaction_id,
                self.pending_interaction.version,
                tuple(
                    (
                        item.target_work_item_id,
                        item.field_name,
                        item.value_schema,
                    )
                    for item in self.pending_interaction.requested_fields
                ),
                tuple(
                    item.fingerprint
                    for item in self.pending_interaction.suspended_work_items
                ),
                self.pending_interaction.checkpoint_thread_id,
            ) if self.pending_interaction else None,
            "pending_approval": (
                self.pending_approval.approval_id,
                self.pending_approval.version,
                self.pending_approval.checkpoint_thread_id,
            ) if self.pending_approval else None,
            "resume": [
                (item.token, item.workstream_id, item.workstream_version)
                for item in self.resume_bindings
            ],
            "consumed": self.consumed_signal_ids,
            "schema": self.schema_version,
            "owner": self.owner.value,
            "human_ticket_ref": self.human_ticket_ref,
            "accepted_approvals": [
                (
                    item.approval_id, item.version, item.workstream_id,
                    item.action_ref, item.operation_key, item.target_entity_ref,
                    item.target_entity_version,
                    tuple((arg.name, arg.value_json) for arg in item.arguments),
                )
                for item in self.accepted_approvals
            ],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "conversation-state:v2:" + hashlib.sha256(raw).hexdigest()

    def start_workstream(self, workstream: WorkstreamState) -> "ConversationState":
        return self.start_workstreams((workstream,))

    def start_workstreams(
        self,
        workstreams: tuple[WorkstreamState, ...],
    ) -> "ConversationState":
        """Accept all plan-level starts as one aggregate transition."""
        if not workstreams:
            raise ConversationStateError("workstream transition is empty")
        current_ids = {item.workstream_id for item in self.workstreams}
        new_ids = tuple(item.workstream_id for item in workstreams)
        if len(new_ids) != len(set(new_ids)) or current_ids.intersection(new_ids):
            raise ConversationStateConflict("workstream already exists")
        return replace(
            self,
            version=self.version + 1,
            workstreams=(*self.workstreams, *workstreams),
        )

    def wait_for_interaction(self, pending: PendingInteractionState) -> "ConversationState":
        if self.pending_interaction is not None or self.pending_approval is not None:
            raise ConversationStateConflict("conversation already has a pending interaction")
        by_id = {item.workstream_id: item for item in self.workstreams}
        requested_ids = {item.target_work_item_id for item in pending.requested_fields}
        suspended_ids = {item.work_item_id for item in pending.suspended_work_items}
        legacy_workstream_ids = requested_ids.difference(suspended_ids)
        if legacy_workstream_ids.difference(by_id):
            raise ConversationStateError(
                "requested field target must be a suspended work item or workstream"
            )
        updated = tuple(
            item.transition(WorkstreamStatus.WAITING_INPUT)
            if item.workstream_id in legacy_workstream_ids else item
            for item in self.workstreams
        )
        rebound = PendingInteractionState(
            pending.interaction_id,
            pending.version,
            pending.requested_fields,
            tuple(
                (item.workstream_id, item.state_version)
                for item in updated if item.workstream_id in legacy_workstream_ids
            ),
            pending.suspended_work_items,
            pending.checkpoint_thread_id,
        )
        return replace(
            self,
            version=self.version + 1,
            workstreams=updated,
            pending_interaction=rebound,
        )

    def wait_for_approval(self, pending: PendingApprovalState) -> "ConversationState":
        if self.pending_interaction is not None or self.pending_approval is not None:
            raise ConversationStateConflict("conversation already has a pending interaction")
        updated = self._transition_workstream(
            pending.workstream_id,
            expected_version=self._workstream(pending.workstream_id).state_version,
            status=WorkstreamStatus.WAITING_APPROVAL,
        )
        return replace(updated, pending_approval=pending)

    def consume_interaction(
        self,
        *,
        interaction_id: str,
        interaction_version: int,
        values: tuple[tuple[str, str, object], ...],
    ) -> "ConversationState":
        pending = self.pending_interaction
        signal_id = f"interaction:{interaction_id}:v{interaction_version}"
        self._assert_unconsumed(signal_id)
        if pending is None or (pending.interaction_id, pending.version) != (
            interaction_id,
            interaction_version,
        ):
            raise ConversationStateConflict("pending interaction changed")
        requested = {
            (item.target_work_item_id, item.field_name)
            for item in pending.requested_fields
        }
        supplied = {(workstream_id, field_name) for workstream_id, field_name, _ in values}
        if supplied != requested:
            raise ConversationStateError("interaction response must fill exactly the requested fields")
        expected_versions = dict(pending.workstream_versions)
        grouped: dict[str, list[ArgumentValue]] = {}
        for workstream_id, field_name, value in values:
            grouped.setdefault(workstream_id, []).append(ArgumentValue.create(field_name, value))
        updated = []
        for item in self.workstreams:
            if item.workstream_id not in grouped:
                updated.append(item)
                continue
            if item.state_version != expected_versions[item.workstream_id]:
                raise ConversationStateConflict("pending interaction target changed")
            updated.append(item.with_slots(tuple(grouped[item.workstream_id])))
        return replace(
            self,
            version=self.version + 1,
            workstreams=tuple(updated),
            pending_interaction=None,
            consumed_signal_ids=(*self.consumed_signal_ids, signal_id),
            resume_bindings=(),
        )

    def consume_approval(
        self,
        *,
        approval_id: str,
        approval_version: int,
        approved: bool,
    ) -> "ConversationState":
        pending = self.pending_approval
        signal_id = f"approval:{approval_id}:v{approval_version}"
        self._assert_unconsumed(signal_id)
        if pending is None or (pending.approval_id, pending.version) != (
            approval_id,
            approval_version,
        ):
            raise ConversationStateConflict("pending approval changed")
        target = WorkstreamStatus.ACTIVE if approved else WorkstreamStatus.CANCELLED
        updated = self._transition_workstream(
            pending.workstream_id,
            expected_version=self._workstream(pending.workstream_id).state_version,
            status=target,
        )
        return replace(
            updated,
            pending_approval=None,
            consumed_signal_ids=(*updated.consumed_signal_ids, signal_id),
            resume_bindings=(),
            accepted_approvals=(
                (*updated.accepted_approvals, AcceptedApprovalState(
                    pending.approval_id,
                    pending.version,
                    pending.workstream_id,
                    pending.action_ref,
                    pending.operation_key,
                    pending.target_entity_ref,
                    pending.target_entity_version,
                    pending.arguments,
                ))
                if approved else updated.accepted_approvals
            ),
        )

    def cancel_workstream(self, workstream_id: str, *, expected_version: int) -> "ConversationState":
        return self._transition_workstream(
            workstream_id,
            expected_version=expected_version,
            status=WorkstreamStatus.CANCELLED,
        )

    def complete_workstream(
        self,
        workstream_id: str,
        *,
        expected_version: int,
    ) -> "ConversationState":
        return self._transition_workstream(
            workstream_id,
            expected_version=expected_version,
            status=WorkstreamStatus.COMPLETED,
            phase="COMPLETE",
        )

    def mark_workstream_reconciling(
        self,
        workstream_id: str,
        *,
        expected_version: int,
    ) -> "ConversationState":
        current = self._workstream(workstream_id)
        if current.status is WorkstreamStatus.RECONCILING:
            if current.state_version != expected_version:
                raise ConversationStateConflict("workstream version changed")
            return self
        return self._transition_workstream(
            workstream_id,
            expected_version=expected_version,
            status=WorkstreamStatus.RECONCILING,
            phase="RECONCILE",
        )

    def transfer_to_human(self, receipt: ReceiptRef) -> "ConversationState":
        if receipt.requirement_id != "support.handoff_action":
            raise ConversationStateError("receipt does not prove a handoff")
        if receipt.effect_status != "COMMITTED":
            raise ConversationStateError("handoff receipt is not committed")
        if self.owner is ConversationOwner.HUMAN:
            if self.human_ticket_ref == receipt.receipt_id:
                return self
            raise ConversationStateConflict("conversation already belongs to another ticket")
        return replace(
            self,
            version=self.version + 1,
            owner=ConversationOwner.HUMAN,
            human_ticket_ref=receipt.receipt_id,
            pending_interaction=None,
            pending_approval=None,
            resume_bindings=(),
            workstreams=tuple(
                item.transition(WorkstreamStatus.PAUSED)
                if not item.terminal else item
                for item in self.workstreams
            ),
        )

    def _transition_workstream(
        self,
        workstream_id: str,
        *,
        expected_version: int,
        status: WorkstreamStatus,
        phase: str | None = None,
    ) -> "ConversationState":
        current = self._workstream(workstream_id)
        if current.state_version != expected_version:
            raise ConversationStateConflict("workstream version changed")
        updated = tuple(
            item.transition(status, phase=phase)
            if item.workstream_id == workstream_id else item
            for item in self.workstreams
        )
        # The aggregate owns every binding to a workstream version.  Invalidate
        # them in the same transition so no intermediate state can reference a
        # version that no longer exists.
        pending_interaction = self.pending_interaction
        if pending_interaction is not None and workstream_id in dict(
            pending_interaction.workstream_versions
        ):
            pending_interaction = None
        pending_approval = self.pending_approval
        if (
            pending_approval is not None
            and pending_approval.workstream_id == workstream_id
            and status is not WorkstreamStatus.WAITING_APPROVAL
        ):
            pending_approval = None
        return replace(
            self,
            version=self.version + 1,
            workstreams=updated,
            pending_interaction=pending_interaction,
            pending_approval=pending_approval,
            resume_bindings=tuple(
                item for item in self.resume_bindings
                if item.workstream_id != workstream_id
            ),
        )

    def _workstream(self, workstream_id: str) -> WorkstreamState:
        try:
            return next(item for item in self.workstreams if item.workstream_id == workstream_id)
        except StopIteration as exc:
            raise ConversationStateError("unknown workstream") from exc

    def _assert_unconsumed(self, signal_id: str) -> None:
        if signal_id in self.consumed_signal_ids:
            raise ConversationStateConflict("signal was already consumed")


class ConversationStateStore(Protocol):
    def load(
        self,
        tenant_id: TenantId,
        user_id: UserId,
        conversation_id: ConversationId,
    ) -> ConversationState: ...

    def compare_and_set(
        self,
        current: ConversationState,
        next_state: ConversationState,
    ) -> bool: ...


class InMemoryConversationStateStore:
    """Thread-safe reference adapter used by contract and graph tests."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str], ConversationState] = {}
        self._lock = RLock()

    def load(
        self,
        tenant_id: TenantId,
        user_id: UserId,
        conversation_id: ConversationId,
    ) -> ConversationState:
        key = (str(tenant_id), str(user_id), str(conversation_id))
        with self._lock:
            return self._states.get(key) or ConversationState.empty(
                tenant_id=key[0], user_id=key[1], conversation_id=key[2],
            )

    def compare_and_set(self, current: ConversationState, next_state: ConversationState) -> bool:
        key = (str(current.tenant_id), str(current.user_id), str(current.conversation_id))
        if key != (
            str(next_state.tenant_id),
            str(next_state.user_id),
            str(next_state.conversation_id),
        ):
            raise ConversationStateError("CAS states belong to different conversations")
        if next_state.version != current.version + 1:
            raise ConversationStateError("CAS must persist exactly one aggregate transition")
        with self._lock:
            stored = self._states.get(key)
            stored_version = stored.version if stored is not None else 0
            if stored_version != current.version:
                return False
            self._states[key] = next_state
            return True


def _required(*values: object) -> None:
    if any(not str(value or "").strip() for value in values):
        raise ConversationStateError("required conversation state field is blank")


def _unique(values: object, label: str) -> None:
    materialized = tuple(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise ConversationStateError(f"{label} must be unique")
