"""Small, non-authoritative understanding artifacts for one turn."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from application.turn_state import (
    ActiveFlowRef,
    FlowDefinitionRef,
    PendingSlotRef,
    TurnStateSnapshot,
)


class UnderstandingError(ValueError):
    pass


class UnderstandingSource(str, Enum):
    PROTOCOL = "PROTOCOL"
    ENCODER = "ENCODER"
    LLM = "LLM"


class CommandKind(str, Enum):
    RESPOND_DIRECT = "RESPOND_DIRECT"
    START_FLOW = "START_FLOW"
    CONTINUE_FLOW = "CONTINUE_FLOW"
    FILL_SLOT = "FILL_SLOT"
    PAUSE_FLOW = "PAUSE_FLOW"
    EXPAND_FLOW = "EXPAND_FLOW"
    SWITCH_FLOW = "SWITCH_FLOW"
    COMPLETE_FLOW = "COMPLETE_FLOW"
    CANCEL_FLOW = "CANCEL_FLOW"
    INTERRUPT_FLOW = "INTERRUPT_FLOW"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    ANSWER_KNOWLEDGE = "ANSWER_KNOWLEDGE"
    REQUEST_HANDOFF = "REQUEST_HANDOFF"


@dataclass(frozen=True)
class CommandArgument:
    name: str
    value_json: str

    @classmethod
    def create(cls, name: str, value: Any) -> "CommandArgument":
        return cls(name, _canonical_json(value))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise UnderstandingError("command argument name is required")
        try:
            parsed = json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise UnderstandingError("command argument must be JSON") from exc
        if _canonical_json(parsed) != self.value_json:
            raise UnderstandingError("command argument must use canonical JSON")

    @property
    def value(self) -> Any:
        return json.loads(self.value_json)


@dataclass(frozen=True)
class CommandShape:
    source_flow: bool
    target_flow: bool
    pending_slot: bool


_COMMAND_SHAPES = {
    CommandKind.START_FLOW: CommandShape(False, True, False),
    CommandKind.CONTINUE_FLOW: CommandShape(True, False, False),
    CommandKind.FILL_SLOT: CommandShape(True, False, True),
    CommandKind.PAUSE_FLOW: CommandShape(True, False, False),
    CommandKind.EXPAND_FLOW: CommandShape(True, True, False),
    CommandKind.SWITCH_FLOW: CommandShape(True, True, False),
    CommandKind.COMPLETE_FLOW: CommandShape(True, False, False),
    CommandKind.CANCEL_FLOW: CommandShape(True, False, False),
    CommandKind.INTERRUPT_FLOW: CommandShape(True, True, False),
    CommandKind.RESPOND_DIRECT: CommandShape(False, False, False),
    CommandKind.REQUEST_CLARIFICATION: CommandShape(False, False, False),
    CommandKind.ANSWER_KNOWLEDGE: CommandShape(False, False, False),
    CommandKind.REQUEST_HANDOFF: CommandShape(False, False, False),
}


@dataclass(frozen=True)
class CommandProposal:
    kind: CommandKind
    source: UnderstandingSource
    message_fingerprint: str
    producer_version: str
    evidence_refs: tuple[str, ...]
    source_flow: ActiveFlowRef | None = None
    target_flow: FlowDefinitionRef | None = None
    pending_slot: PendingSlotRef | None = None
    arguments: tuple[CommandArgument, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, CommandKind):
            raise UnderstandingError("unsupported command kind")
        if not isinstance(self.source, UnderstandingSource):
            raise UnderstandingError("unsupported understanding source")
        _sha256(self.message_fingerprint, "message_fingerprint")
        if not self.producer_version.strip():
            raise UnderstandingError("producer_version is required")
        for name in ("evidence_refs", "arguments"):
            if not isinstance(getattr(self, name), tuple):
                raise UnderstandingError(f"{name} must be an immutable tuple")
        if not self.evidence_refs or any(not item.strip() for item in self.evidence_refs):
            raise UnderstandingError("command evidence is required")
        if len(self.evidence_refs) != len(set(self.evidence_refs)):
            raise UnderstandingError("command evidence must be unique")
        if len({item.name for item in self.arguments}) != len(self.arguments):
            raise UnderstandingError("command arguments must be unique")
        shape = _COMMAND_SHAPES[self.kind]
        actual = (
            self.source_flow is not None,
            self.target_flow is not None,
            self.pending_slot is not None,
        )
        expected = (shape.source_flow, shape.target_flow, shape.pending_slot)
        if actual != expected:
            raise UnderstandingError(
                f"{self.kind.value} requires source/target/signal={expected}"
            )
        if self.kind is CommandKind.FILL_SLOT and {
            item.name for item in self.arguments
        } != {"field_name", "field_value"}:
            raise UnderstandingError("FILL_SLOT requires field_name and field_value")

    @property
    def proposal_id(self) -> str:
        return "command-proposal:v1:" + _fingerprint({
            "kind": self.kind.value,
            "source": self.source.value,
            "message": self.message_fingerprint,
            "producer": self.producer_version,
            "evidence": sorted(self.evidence_refs),
            "source_flow": (
                self.source_flow.instance_id if self.source_flow else ""
            ),
            "source_flow_version": (
                self.source_flow.state_version if self.source_flow else None
            ),
            "target_flow": self.target_flow.key if self.target_flow else None,
            "signal": (
                (self.pending_slot.slot_id, self.pending_slot.slot_version)
                if self.pending_slot else None
            ),
            "arguments": [
                (item.name, item.value_json)
                for item in sorted(self.arguments, key=lambda value: value.name)
            ],
        })


class UnderstandingStatus(str, Enum):
    RESOLVED = "RESOLVED"
    DEFER = "DEFER"
    CLARIFY = "CLARIFY"
    NO_SUPPORTED_FLOW = "NO_SUPPORTED_FLOW"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"


@dataclass(frozen=True)
class UnderstandingResult:
    status: UnderstandingStatus
    commands: tuple[CommandProposal, ...] = ()
    reason_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, UnderstandingStatus):
            raise UnderstandingError("unsupported understanding status")
        if not isinstance(self.commands, tuple):
            raise UnderstandingError("commands must be an immutable tuple")
        if self.status is UnderstandingStatus.RESOLVED:
            if not self.commands:
                raise UnderstandingError("RESOLVED requires commands")
        elif self.commands:
            raise UnderstandingError(f"{self.status.value} cannot carry commands")
        if self.status is not UnderstandingStatus.RESOLVED and not self.reason_code:
            raise UnderstandingError(f"{self.status.value} requires a reason")


class PendingSlotResolver:
    """Resolve only a signal-bound slot; all other messages are deferred."""

    def __init__(
        self,
        parse_value: Callable[[PendingSlotRef, str], Any | None],
        *,
        producer_version: str = "pending-slot-resolver-v1",
    ) -> None:
        self._parse_value = parse_value
        self._producer_version = producer_version

    def resolve(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
    ) -> UnderstandingResult:
        slot = state.pending_slot
        if slot is None:
            return UnderstandingResult(
                UnderstandingStatus.DEFER,
                reason_code="NO_PROTOCOL_BINDING",
            )
        value = self._parse_value(slot, message)
        if value is None:
            return UnderstandingResult(
                UnderstandingStatus.CLARIFY,
                reason_code="PENDING_SLOT_VALUE_INVALID",
            )
        source_flow = next(
            item for item in state.active_flows
            if item.instance_id == slot.flow_instance_id
        )
        proposal = CommandProposal(
            kind=CommandKind.FILL_SLOT,
            source=UnderstandingSource.PROTOCOL,
            message_fingerprint=message_fingerprint,
            producer_version=self._producer_version,
            evidence_refs=(
                f"message:{message_fingerprint}",
                f"pending-slot:{slot.slot_id}:{slot.slot_version}",
            ),
            source_flow=source_flow,
            pending_slot=slot,
            arguments=(
                CommandArgument.create("field_name", slot.field_name),
                CommandArgument.create("field_value", value),
            ),
        )
        return UnderstandingResult(UnderstandingStatus.RESOLVED, (proposal,))


def fingerprint_message(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def _sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise UnderstandingError(f"{name} must be a lowercase SHA-256")


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise UnderstandingError("command value must be finite JSON") from exc


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
