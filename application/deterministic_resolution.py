"""Resolve only state-bound or explicitly parsed turn signals without an LLM."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from application.conversation_state import ConversationState


class DeterministicResolutionError(ValueError):
    pass


class ExplicitControlSignal(str, Enum):
    CANCEL = "CANCEL"
    CONTINUE = "CONTINUE"


class ResolutionKind(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    FILL_PENDING_INPUT = "FILL_PENDING_INPUT"
    APPROVAL_DECISION = "APPROVAL_DECISION"
    RESUME_WORKSTREAM = "RESUME_WORKSTREAM"
    CANCEL_WORKSTREAM = "CANCEL_WORKSTREAM"
    CONTINUE_WORKSTREAM = "CONTINUE_WORKSTREAM"
    CLARIFY_WORKSTREAM = "CLARIFY_WORKSTREAM"


@dataclass(frozen=True)
class TurnObservations:
    raw_text: str
    structured_fields: tuple[tuple[str, object], ...] = ()
    approval_decision: bool | None = None
    approval_id: str | None = None
    resume_token: str | None = None
    explicit_control: ExplicitControlSignal | None = None
    target_workstream_id: str | None = None

    def __post_init__(self) -> None:
        names = tuple(item[0] for item in self.structured_fields)
        if any(not item.strip() for item in names) or len(names) != len(set(names)):
            raise DeterministicResolutionError("structured fields must have unique names")
        if self.approval_decision is not None and not str(self.approval_id or "").strip():
            raise DeterministicResolutionError("approval decision requires approval identity")


@dataclass(frozen=True)
class ResolvedField:
    workstream_id: str
    field_name: str
    value: object


@dataclass(frozen=True)
class DeterministicResolution:
    kind: ResolutionKind
    reason_code: str
    state_fingerprint: str
    workstream_id: str | None = None
    expected_workstream_version: int | None = None
    signal_id: str | None = None
    signal_version: int | None = None
    approved: bool | None = None
    fields: tuple[ResolvedField, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.kind is not ResolutionKind.UNRESOLVED


class DeterministicResolver:
    version = "deterministic-resolver-v1"

    def resolve(
        self,
        observations: TurnObservations,
        state: ConversationState,
    ) -> DeterministicResolution:
        pending = state.pending_interaction
        if pending is not None:
            fields = self._bind_pending_fields(observations, pending.requested_fields)
            if fields is not None:
                return DeterministicResolution(
                    ResolutionKind.FILL_PENDING_INPUT,
                    "PENDING_FIELDS_BOUND",
                    state.fingerprint,
                    signal_id=pending.interaction_id,
                    signal_version=pending.version,
                    fields=fields,
                )

        approval = state.pending_approval
        if approval is not None and observations.approval_decision is not None:
            if observations.approval_id != approval.approval_id:
                raise DeterministicResolutionError("approval reply targets another interaction")
            stream = next(
                item for item in state.workstreams
                if item.workstream_id == approval.workstream_id
            )
            return DeterministicResolution(
                ResolutionKind.APPROVAL_DECISION,
                "PENDING_APPROVAL_BOUND",
                state.fingerprint,
                stream.workstream_id,
                stream.state_version,
                approval.approval_id,
                approval.version,
                observations.approval_decision,
            )

        if observations.resume_token is not None:
            matches = tuple(
                item for item in state.resume_bindings
                if item.token == observations.resume_token
            )
            if not matches:
                raise DeterministicResolutionError("resume token is stale or unknown")
            binding = matches[0]
            return DeterministicResolution(
                ResolutionKind.RESUME_WORKSTREAM,
                "RESUME_TOKEN_BOUND",
                state.fingerprint,
                binding.workstream_id,
                binding.workstream_version,
            )

        if observations.explicit_control is not None:
            candidates = state.active_workstreams
            if observations.target_workstream_id is not None:
                candidates = tuple(
                    item for item in candidates
                    if item.workstream_id == observations.target_workstream_id
                )
            if len(candidates) > 1:
                return DeterministicResolution(
                    ResolutionKind.CLARIFY_WORKSTREAM,
                    "CONTROL_TARGET_AMBIGUOUS",
                    state.fingerprint,
                )
            if len(candidates) == 1:
                stream = candidates[0]
                kind = (
                    ResolutionKind.CANCEL_WORKSTREAM
                    if observations.explicit_control is ExplicitControlSignal.CANCEL
                    else ResolutionKind.CONTINUE_WORKSTREAM
                )
                return DeterministicResolution(
                    kind,
                    "EXPLICIT_CONTROL_BOUND",
                    state.fingerprint,
                    stream.workstream_id,
                    stream.state_version,
                )

        if len(state.active_workstreams) == 1 and observations.raw_text.strip():
            stream = state.active_workstreams[0]
            return DeterministicResolution(
                ResolutionKind.CONTINUE_WORKSTREAM,
                "UNIQUE_ACTIVE_WORKSTREAM",
                state.fingerprint,
                stream.workstream_id,
                stream.state_version,
            )
        return DeterministicResolution(
            ResolutionKind.UNRESOLVED,
            "NO_DETERMINISTIC_BINDING",
            state.fingerprint,
        )

    @staticmethod
    def _bind_pending_fields(observations, requested_fields):
        provided = dict(observations.structured_fields)
        if len(requested_fields) == 1 and not provided and observations.raw_text.strip():
            requested = requested_fields[0]
            return (ResolvedField(
                requested.target_work_item_id,
                requested.field_name,
                observations.raw_text.strip(),
            ),)
        names = {item.field_name for item in requested_fields}
        if names != set(provided):
            return None
        return tuple(
            ResolvedField(item.target_work_item_id, item.field_name, provided[item.field_name])
            for item in requested_fields
        )

