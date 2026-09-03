"""Finite state read before optional semantic routing.

The state loader owns this snapshot.  It contains only the small amount of
authoritative state needed to interpret one inbound turn; long-term semantic
memory remains an optional evidence provider.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class TurnStateError(ValueError):
    pass


@dataclass(frozen=True)
class PrincipalScope:
    tenant_id: str
    user_id: str
    conversation_id: str

    def __post_init__(self) -> None:
        _nonblank(self.tenant_id, "tenant_id")
        _nonblank(self.user_id, "user_id")
        _nonblank(self.conversation_id, "conversation_id")

    @property
    def fingerprint(self) -> str:
        return _fingerprint({
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
        })


class StateAvailability(str, Enum):
    CURRENT = "CURRENT"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class StateSourceStatus:
    source: str
    availability: StateAvailability
    version: str
    reason_code: str = ""

    def __post_init__(self) -> None:
        _nonblank(self.source, "state source")
        _nonblank(self.version, "state source version")
        if not isinstance(self.availability, StateAvailability):
            raise TurnStateError("unsupported state availability")
        if self.availability is not StateAvailability.CURRENT:
            _nonblank(self.reason_code, "non-current source reason")


@dataclass(frozen=True)
class FlowDefinitionRef:
    flow_id: str
    version: str

    def __post_init__(self) -> None:
        _nonblank(self.flow_id, "flow_id")
        _nonblank(self.version, "flow version")

    @property
    def key(self) -> tuple[str, str]:
        return self.flow_id, self.version


@dataclass(frozen=True)
class FlowBinding:
    name: str
    value_json: str

    @classmethod
    def create(cls, name: str, value: object) -> "FlowBinding":
        return cls(name, json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ))

    def __post_init__(self) -> None:
        _nonblank(self.name, "flow binding name")
        try:
            parsed = json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise TurnStateError("flow binding value must be JSON") from exc
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if canonical != self.value_json:
            raise TurnStateError("flow binding value must use canonical JSON")

    @property
    def value(self) -> object:
        return json.loads(self.value_json)


@dataclass(frozen=True)
class ActiveFlowRef:
    definition: FlowDefinitionRef
    instance_id: str
    state_version: int
    principal_fingerprint: str
    bindings: tuple[FlowBinding, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.definition, FlowDefinitionRef):
            raise TurnStateError("active flow requires a definition")
        _nonblank(self.instance_id, "flow instance_id")
        _positive_int(self.state_version, "flow state_version")
        _sha256(self.principal_fingerprint, "flow principal_fingerprint")
        if not isinstance(self.bindings, tuple):
            raise TurnStateError("flow bindings must be an immutable tuple")
        _unique((item.name for item in self.bindings), "flow bindings")


class PendingInputKind(str, Enum):
    SLOT_VALUE = "SLOT_VALUE"
    APPROVAL = "APPROVAL"
    RESUME = "RESUME"


@dataclass(frozen=True)
class PendingSignalRef:
    signal_id: str
    signal_version: int
    kind: PendingInputKind
    flow_instance_id: str
    field_name: str
    principal_fingerprint: str

    def __post_init__(self) -> None:
        _nonblank(self.signal_id, "signal_id")
        _positive_int(self.signal_version, "signal_version")
        if not isinstance(self.kind, PendingInputKind):
            raise TurnStateError("unsupported pending input kind")
        _nonblank(self.flow_instance_id, "signal flow_instance_id")
        _nonblank(self.field_name, "signal field_name")
        _sha256(self.principal_fingerprint, "signal principal_fingerprint")


@dataclass(frozen=True)
class FlowAggregateVersion:
    aggregate_id: str
    version: int

    def __post_init__(self) -> None:
        _nonblank(self.aggregate_id, "flow aggregate_id")
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise TurnStateError("flow aggregate version must be an integer")
        if self.version < 0:
            raise TurnStateError("flow aggregate version must be non-negative")


@dataclass(frozen=True)
class TurnStateSnapshot:
    request_id: str
    principal: PrincipalScope
    flow_aggregate: FlowAggregateVersion | None
    active_flows: tuple[ActiveFlowRef, ...]
    pending_signal: PendingSignalRef | None
    recent_turn_refs: tuple[str, ...]
    active_case_refs: tuple[str, ...]
    reusable_media_refs: tuple[str, ...]
    sources: tuple[StateSourceStatus, ...]
    captured_at: datetime
    producer_version: str = "turn-state-loader-v1"

    def __post_init__(self) -> None:
        _nonblank(self.request_id, "request_id")
        if not isinstance(self.principal, PrincipalScope):
            raise TurnStateError("snapshot requires an authenticated principal")
        if self.flow_aggregate is not None and not isinstance(
            self.flow_aggregate, FlowAggregateVersion,
        ):
            raise TurnStateError("flow_aggregate must be a versioned reference")
        for name in (
            "active_flows",
            "recent_turn_refs",
            "active_case_refs",
            "reusable_media_refs",
            "sources",
        ):
            if not isinstance(getattr(self, name), tuple):
                raise TurnStateError(f"{name} must be an immutable tuple")
        if not isinstance(self.captured_at, datetime) or (
            self.captured_at.utcoffset() is None
        ):
            raise TurnStateError("captured_at must be timezone-aware")
        _nonblank(self.producer_version, "state producer_version")
        _unique((item.instance_id for item in self.active_flows), "active flows")
        _unique((item.source for item in self.sources), "state sources")
        if any(
            item.principal_fingerprint != self.principal.fingerprint
            for item in self.active_flows
        ):
            raise TurnStateError("active flow belongs to another principal")
        if self.pending_signal is not None:
            if self.pending_signal.principal_fingerprint != self.principal.fingerprint:
                raise TurnStateError("pending signal belongs to another principal")
            if self.pending_signal.flow_instance_id not in {
                item.instance_id for item in self.active_flows
            }:
                raise TurnStateError("pending signal is not bound to an active flow")

    @property
    def fingerprint(self) -> str:
        return _fingerprint({
            "request_id": self.request_id,
            "principal": self.principal.fingerprint,
            "flow_aggregate": (
                {
                    "id": self.flow_aggregate.aggregate_id,
                    "version": self.flow_aggregate.version,
                }
                if self.flow_aggregate else None
            ),
            "active_flows": [
                {
                    "flow": item.definition.key,
                    "instance_id": item.instance_id,
                    "state_version": item.state_version,
                    "bindings": [
                        (binding.name, binding.value_json)
                        for binding in sorted(
                            item.bindings,
                            key=lambda value: value.name,
                        )
                    ],
                }
                for item in sorted(self.active_flows, key=lambda value: value.instance_id)
            ],
            "pending_signal": (
                {
                    "id": self.pending_signal.signal_id,
                    "version": self.pending_signal.signal_version,
                    "kind": self.pending_signal.kind.value,
                    "flow": self.pending_signal.flow_instance_id,
                    "field": self.pending_signal.field_name,
                }
                if self.pending_signal else None
            ),
            "recent_turn_refs": self.recent_turn_refs,
            "active_case_refs": self.active_case_refs,
            "reusable_media_refs": self.reusable_media_refs,
            "sources": [
                {
                    "source": item.source,
                    "availability": item.availability.value,
                    "version": item.version,
                    "reason": item.reason_code,
                }
                for item in sorted(self.sources, key=lambda value: value.source)
            ],
            "captured_at": self.captured_at.astimezone(timezone.utc).isoformat(),
            "producer_version": self.producer_version,
        })


def _nonblank(value: object, name: str) -> None:
    if not str(value or "").strip():
        raise TurnStateError(f"{name} is required")


def _positive_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TurnStateError(f"{name} must be a positive integer")


def _sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise TurnStateError(f"{name} must be a lowercase SHA-256")


def _unique(values: object, name: str) -> None:
    materialized = tuple(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise TurnStateError(f"{name} must be unique")


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
