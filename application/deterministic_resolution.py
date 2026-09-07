"""Resolve only state-bound or explicitly parsed turn signals without an LLM."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from application.conversation_state import ConversationState
from application.entity_binding import BindingSource, EntityBinding
from application.work_item import ArgumentValue, ControlMode, WorkItem


class DeterministicResolutionError(ValueError):
    pass


class ExplicitControlSignal(str, Enum):
    CANCEL = "CANCEL"
    CONTINUE = "CONTINUE"


class ResolutionKind(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    FILL_PENDING_INPUT = "FILL_PENDING_INPUT"
    REPLY_PENDING_INPUT = "REPLY_PENDING_INPUT"
    APPROVAL_DECISION = "APPROVAL_DECISION"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    RECONCILE_WORKFLOW = "RECONCILE_WORKFLOW"
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
    interaction_id: str | None = None
    interaction_version: int | None = None
    interaction_values: tuple[tuple[str, str, object], ...] = ()
    understanding_evidence: tuple[tuple[str, object], ...] = ()

    def __post_init__(self) -> None:
        names = tuple(item[0] for item in self.structured_fields)
        if any(not item.strip() for item in names) or len(names) != len(set(names)):
            raise DeterministicResolutionError("structured fields must have unique names")
        if self.approval_decision is not None and not str(self.approval_id or "").strip():
            raise DeterministicResolutionError("approval decision requires approval identity")
        if self.approval_id is not None and self.approval_decision is None:
            raise DeterministicResolutionError("approval identity requires a decision")
        interaction_keys = tuple(
            (work_item_id, field_name)
            for work_item_id, field_name, _ in self.interaction_values
        )
        if len(interaction_keys) != len(set(interaction_keys)):
            raise DeterministicResolutionError("interaction values must be unique")
        if self.interaction_values and not all((
            str(self.interaction_id or "").strip(),
            self.interaction_version is not None,
        )):
            raise DeterministicResolutionError(
                "interaction values require interaction identity and version"
            )
        evidence_kinds = tuple(item[0] for item in self.understanding_evidence)
        if any(not item.strip() for item in evidence_kinds):
            raise DeterministicResolutionError("understanding evidence kind is required")


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
    action_ref: str | None = None
    operation_key: str | None = None
    target_entity_ref: str | None = None
    target_entity_version: str | None = None
    arguments: tuple[tuple[str, object], ...] = ()
    resumed_work_items: tuple[WorkItem, ...] = ()
    argument_bindings: tuple[EntityBinding, ...] = ()
    action_origin_work_item_id: str | None = None

    @property
    def closed_work_items(self) -> tuple[WorkItem, ...]:
        """The declined bound action closes its origin and dependent objectives."""
        if self.kind not in {ResolutionKind.APPROVAL_DECISION, ResolutionKind.APPROVAL_EXPIRED} or self.approved:
            return ()
        excluded = {self.action_origin_work_item_id} if self.action_origin_work_item_id else set()
        while True:
            expanded = excluded | {item.work_item_id for item in self.resumed_work_items
                                   if excluded.intersection(item.dependencies)}
            if expanded == excluded:
                break
            excluded = expanded
        return tuple(item for item in self.resumed_work_items if item.work_item_id in excluded)

    @property
    def resolved(self) -> bool:
        return self.kind is not ResolutionKind.UNRESOLVED


class DeterministicResolver:
    version = "deterministic-resolver-v1"

    def __init__(self, clock=None) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def resolve(
        self,
        observations: TurnObservations,
        state: ConversationState,
    ) -> DeterministicResolution:
        pending = state.pending_interaction
        if pending is not None and observations.approval_decision is not None:
            raise DeterministicResolutionError("resolve the pending clarification before deciding approval")
        if pending is not None:
            if observations.interaction_id is not None and (
                observations.interaction_id != pending.interaction_id
                or observations.interaction_version != pending.version
            ):
                raise DeterministicResolutionError(
                    "interaction reply targets another interaction"
                )
            # A bound free-text reply resumes domain reasoning; it is not a
            # verified value for every requested field (or even a single field).
            if (observations.interaction_id is not None
                    and observations.raw_text.strip()
                    and not observations.interaction_values
                    and not observations.structured_fields
                    and pending.suspended_work_items
                    and all(item.control_mode is ControlMode.DELEGATED
                            for item in pending.suspended_work_items
                            if item.work_item_id in {field.target_work_item_id for field in pending.requested_fields})):
                return DeterministicResolution(
                    ResolutionKind.REPLY_PENDING_INPUT, "PENDING_REPLY_BOUND",
                    state.fingerprint, signal_id=pending.interaction_id,
                    signal_version=pending.version,
                    resumed_work_items=pending.suspended_work_items,
                )
            fields = self._bind_pending_fields(observations, pending.requested_fields)
            if fields is not None:
                if observations.interaction_id is None:
                    raise DeterministicResolutionError(
                        "pending input requires interaction identity"
                    )
                return DeterministicResolution(
                    ResolutionKind.FILL_PENDING_INPUT,
                    "PENDING_FIELDS_BOUND",
                    state.fingerprint,
                    signal_id=pending.interaction_id,
                    signal_version=pending.version,
                    fields=fields,
                    resumed_work_items=self._resume_work_items(
                        pending.suspended_work_items, fields, state,
                    ),
                )

        approval = state.pending_approval
        if approval is not None and observations.approval_decision is not None:
            if observations.approval_id != approval.approval_id:
                raise DeterministicResolutionError("approval reply targets another interaction")
            if self._clock() >= datetime.fromisoformat(approval.expires_at):
                return DeterministicResolution(
                    ResolutionKind.APPROVAL_EXPIRED,
                    "PENDING_APPROVAL_EXPIRED",
                    state.fingerprint,
                    approval.workstream_id,
                    next(
                        item.state_version for item in state.workstreams
                        if item.workstream_id == approval.workstream_id
                    ),
                    approval.approval_id,
                    approval.version,
                    False,
                    resumed_work_items=approval.suspended_work_items,
                    action_origin_work_item_id=approval.origin_work_item_id,
                )
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
                action_ref=approval.action_ref,
                operation_key=approval.operation_key,
                target_entity_ref=approval.target_entity_ref,
                target_entity_version=approval.target_entity_version,
                arguments=tuple((item.name, item.value) for item in approval.arguments),
                argument_bindings=approval.argument_bindings,
                resumed_work_items=approval.suspended_work_items,
                action_origin_work_item_id=approval.origin_work_item_id,
            )
        if observations.approval_decision is not None:
            accepted = next((
                item for item in state.accepted_approvals
                if item.approval_id == observations.approval_id
            ), None)
            stream = next((
                item for item in state.active_workstreams
                if accepted is not None and item.workstream_id == accepted.workstream_id
            ), None)
            if accepted is not None and stream is not None and observations.approval_decision:
                return DeterministicResolution(
                    ResolutionKind.RECONCILE_WORKFLOW,
                    "ACCEPTED_OPERATION_RECONCILIATION",
                    state.fingerprint,
                    stream.workstream_id,
                    stream.state_version,
                    accepted.approval_id,
                    accepted.version,
                    True,
                    action_ref=accepted.action_ref,
                    operation_key=accepted.operation_key,
                    target_entity_ref=accepted.target_entity_ref,
                    target_entity_version=accepted.target_entity_version,
                    arguments=tuple(
                        (item.name, item.value) for item in accepted.arguments
                    ),
                    argument_bindings=accepted.argument_bindings,
                )
            raise DeterministicResolutionError("approval signal is stale or unknown")

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

        return DeterministicResolution(
            ResolutionKind.UNRESOLVED,
            "NO_DETERMINISTIC_BINDING",
            state.fingerprint,
        )

    @staticmethod
    def _bind_pending_fields(observations, requested_fields):
        if observations.interaction_values:
            provided = {
                (work_item_id, field_name): value
                for work_item_id, field_name, value in observations.interaction_values
            }
            requested = {
                (item.target_work_item_id, item.field_name)
                for item in requested_fields
            }
            if set(provided) != requested:
                return None
            return tuple(
                ResolvedField(
                    item.target_work_item_id,
                    item.field_name,
                    provided[(item.target_work_item_id, item.field_name)],
                )
                for item in requested_fields
            )
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

    @staticmethod
    def _resume_work_items(
        suspended: tuple[WorkItem, ...],
        fields: tuple[ResolvedField, ...],
        state: ConversationState,
    ) -> tuple[WorkItem, ...]:
        values: dict[str, list[ArgumentValue]] = {}
        for field in fields:
            values.setdefault(field.workstream_id, []).append(
                ArgumentValue.create(field.field_name, field.value)
            )
        resumed = []
        for item in suspended:
            merged = {argument.name: argument for argument in item.arguments}
            merged.update({argument.name: argument for argument in values.get(
                item.work_item_id, (),
            )})
            new_bindings = tuple(EntityBinding.create(
                field.field_name, field.value,
                source=BindingSource.PENDING_INTERACTION,
                source_ref=(
                    f"interaction:{state.pending_interaction.interaction_id}:"
                    f"v{state.pending_interaction.version}:"
                    f"{field.workstream_id}:{field.field_name}"
                ),
                tenant_id=str(state.tenant_id), user_id=str(state.user_id),
                conversation_id=str(state.conversation_id), priority=500,
            ) for field in fields if field.workstream_id == item.work_item_id)
            rebound = {
                binding.field_name: binding for binding in item.argument_bindings
            }
            rebound.update({binding.field_name: binding for binding in new_bindings})
            resumed.append(WorkItem(**{
                **item.__dict__,
                "arguments": tuple(merged[name] for name in sorted(merged)),
                "argument_bindings": tuple(
                    rebound[name] for name in sorted(rebound)
                ),
            }))
        return tuple(resumed)
