"""Conversation-scoped active-flow state and its optimistic-write port."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from application.turn_state import (
    ActiveFlowRef,
    FlowAggregateVersion,
    PendingInputKind,
    PendingSignalRef,
    PrincipalScope,
)


class FlowStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class FlowStateAggregate:
    principal: PrincipalScope
    aggregate: FlowAggregateVersion
    active_flows: tuple[ActiveFlowRef, ...]
    pending_slot: PendingSignalRef | None
    deletion_epoch: int
    schema_version: str = "conversation-flow-state-v1"

    def __post_init__(self) -> None:
        if self.deletion_epoch < 0:
            raise FlowStateError("deletion_epoch must be non-negative")
        if any(
            flow.principal_fingerprint != self.principal.fingerprint
            for flow in self.active_flows
        ):
            raise FlowStateError("active flow and principal differ")
        if self.pending_slot is not None:
            if self.pending_slot.kind is not PendingInputKind.SLOT_VALUE:
                raise FlowStateError("flow state only owns pending slot values")
            if self.pending_slot.principal_fingerprint != self.principal.fingerprint:
                raise FlowStateError("pending slot and principal differ")
            if self.pending_slot.flow_instance_id not in {
                flow.instance_id for flow in self.active_flows
            }:
                raise FlowStateError("pending slot has no active flow")

    @classmethod
    def empty(
        cls,
        principal: PrincipalScope,
        *,
        deletion_epoch: int,
    ) -> "FlowStateAggregate":
        identity = ":".join((
            principal.tenant_id,
            principal.user_id,
            principal.conversation_id,
        ))
        aggregate_id = "flow-state:v1:" + hashlib.sha256(
            identity.encode("utf-8")
        ).hexdigest()
        return cls(
            principal,
            FlowAggregateVersion(aggregate_id, 0),
            (),
            None,
            deletion_epoch,
        )

    def next(
        self,
        *,
        active_flows: tuple[ActiveFlowRef, ...],
        pending_slot: PendingSignalRef | None,
    ) -> "FlowStateAggregate":
        return FlowStateAggregate(
            self.principal,
            FlowAggregateVersion(
                self.aggregate.aggregate_id,
                self.aggregate.version + 1,
            ),
            active_flows,
            pending_slot,
            self.deletion_epoch,
            self.schema_version,
        )

    def advance_flow(
        self,
        instance_id: str,
        *,
        expected_version: int,
    ) -> "FlowStateAggregate":
        matched = False
        active_flows = []
        for flow in self.active_flows:
            if flow.instance_id != instance_id:
                active_flows.append(flow)
                continue
            if flow.state_version != expected_version:
                raise FlowStateError("active flow version changed")
            matched = True
            active_flows.append(ActiveFlowRef(
                flow.definition,
                flow.instance_id,
                flow.state_version + 1,
                flow.principal_fingerprint,
                flow.bindings,
            ))
        if not matched:
            raise FlowStateError("active flow is unavailable")
        return self.next(
            active_flows=tuple(active_flows),
            pending_slot=self.pending_slot,
        )


class FlowStateStore(Protocol):
    def load(self, principal: PrincipalScope) -> FlowStateAggregate: ...

    def compare_and_set(
        self,
        current: FlowStateAggregate,
        next_state: FlowStateAggregate,
    ) -> bool: ...
