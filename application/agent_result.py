"""Typed worker outputs and cross-agent information contracts."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from application.work_item import ArgumentValue, WorkItem


class AgentResultContractError(ValueError):
    pass


class AgentResultStatus(str, Enum):
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    NEEDS_EVIDENCE = "NEEDS_EVIDENCE"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    RECONCILING = "RECONCILING"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class FactSourceKind(str, Enum):
    VERIFIED_STATE = "VERIFIED_STATE"
    USER_ASSERTED = "USER_ASSERTED"
    MEDIA_OBSERVED = "MEDIA_OBSERVED"
    KNOWLEDGE_ASSERTED = "KNOWLEDGE_ASSERTED"
    DERIVED = "DERIVED"


@dataclass(frozen=True)
class FactRecord:
    subject_ref: str
    requirement_id: str
    value_json: str
    source_kind: FactSourceKind
    source_ref: str
    producer_id: str
    producer_version: str
    observed_at: datetime
    valid_until: datetime | None = None
    observation_started_at: datetime | None = None

    @property
    def observation_signature(self):
        return (self.value_json, self.source_kind, self.valid_until, self.observation_started_at,
                self.observed_at if self.observation_started_at is not None else None)

    def __post_init__(self) -> None:
        if any(not str(value or "").strip() for value in (
            self.subject_ref,
            self.requirement_id,
            self.value_json,
            self.source_ref,
            self.producer_id,
            self.producer_version,
        )):
            raise AgentResultContractError("fact identity, value, and provenance are required")
        _canonical_json(self.value_json, "fact value")
        if self.observed_at.utcoffset() is None:
            raise AgentResultContractError("fact observation time must be timezone-aware")
        if self.observation_started_at is not None and (
            self.observation_started_at.utcoffset() is None or self.observation_started_at > self.observed_at
        ):
            raise AgentResultContractError("fact observation interval must be aware and ordered")
        if self.valid_until is not None:
            if self.valid_until.utcoffset() is None:
                raise AgentResultContractError("fact validity time must be timezone-aware")
            if self.valid_until < self.observed_at:
                raise AgentResultContractError("fact cannot expire before observation")


def merge_facts(*groups: tuple[FactRecord, ...]) -> tuple[FactRecord, ...]:
    merged = {}
    for fact in (fact for group in groups for fact in group):
        key = (fact.subject_ref, fact.requirement_id, fact.source_ref, fact.producer_id, fact.producer_version)
        prior = merged.setdefault(key, fact)
        if prior.observation_signature != fact.observation_signature:
            raise AgentResultContractError("one evidence identity produced conflicting values or observation metadata")
    return tuple(merged.values())


@dataclass(frozen=True)
class EvidenceRequest:
    requirement_id: str
    target_work_item_id: str
    preferred_providers: tuple[str, ...]
    arguments: tuple[ArgumentValue, ...] = ()

    def __post_init__(self) -> None:
        _required(self.requirement_id, self.target_work_item_id)
        _unique_nonblank(self.preferred_providers, "evidence providers")
        if not self.preferred_providers:
            raise AgentResultContractError("evidence request requires providers")
        names = tuple(item.name for item in self.arguments)
        if len(names) != len(set(names)):
            raise AgentResultContractError("evidence arguments must be unique")


@dataclass(frozen=True)
class MissingInputSpec:
    field_name: str
    target_work_item_id: str
    reason_code: str
    value_schema: str
    question_hint: str
    required: bool = True

    def __post_init__(self) -> None:
        _required(
            self.field_name,
            self.target_work_item_id,
            self.reason_code,
            self.value_schema,
            self.question_hint,
        )


@dataclass(frozen=True)
class ReceiptRef:
    receipt_id: str
    schema_version: str
    operation_key: str
    effect_status: str
    requirement_id: str

    def __post_init__(self) -> None:
        _required(
            self.receipt_id,
            self.schema_version,
            self.operation_key,
            self.effect_status,
            self.requirement_id,
        )


@dataclass(frozen=True)
class StateMutationProposal:
    mutation_kind: str
    workstream_id: str
    expected_version: int
    payload_json: str
    apply_after: str

    def __post_init__(self) -> None:
        _required(
            self.mutation_kind,
            self.workstream_id,
            self.payload_json,
            self.apply_after,
        )
        if self.expected_version < 0:
            raise AgentResultContractError("mutation version must be non-negative")
        _canonical_json(self.payload_json, "mutation payload")


@dataclass(frozen=True)
class AgentResult:
    work_item_id: str
    owner_agent: str
    status: AgentResultStatus
    reason_code: str
    producer_version: str
    facts: tuple[FactRecord, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    action_receipts: tuple[ReceiptRef, ...] = ()
    missing_inputs: tuple[MissingInputSpec, ...] = ()
    requested_evidence: tuple[EvidenceRequest, ...] = ()
    state_mutation_proposals: tuple[StateMutationProposal, ...] = ()
    # Internal domain explanation, not a publishable answer or a fact source.
    # Keep the persisted field name so suspended work retains its original schema.
    candidate_response: str | None = None
    retryable: bool = False
    pending_action: WorkItem | None = None
    # Framework message snapshot for a validated continuation. Never a public
    # response or fact source; persisted by the existing parent checkpointer.
    working_messages: tuple[dict, ...] = ()
    # Diagnostics survive message compaction; they are not business evidence.
    execution_feedback: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        for name in ("facts", "evidence_refs", "action_receipts", "missing_inputs",
                     "requested_evidence", "state_mutation_proposals", "working_messages",
                     "execution_feedback"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        _required(
            self.work_item_id,
            self.owner_agent,
            self.reason_code,
            self.producer_version,
        )
        _unique_nonblank(self.evidence_refs, "evidence refs")
        if self.pending_action is not None:
            if self.status is not AgentResultStatus.WAITING_APPROVAL:
                raise AgentResultContractError("pending action requires WAITING_APPROVAL")
            if self.pending_action.effect.value != "WRITE" or self.pending_action.owner_agent != self.owner_agent:
                raise AgentResultContractError("pending action must be an owned business write")
        if any(item.target_work_item_id != self.work_item_id for item in self.missing_inputs):
            raise AgentResultContractError("missing input targets another work item")
        if any(item.target_work_item_id != self.work_item_id for item in self.requested_evidence):
            raise AgentResultContractError("evidence request targets another work item")
        if self.status is AgentResultStatus.NEEDS_USER_INPUT and not self.missing_inputs:
            raise AgentResultContractError("NEEDS_USER_INPUT requires missing inputs")
        if (
            self.status is AgentResultStatus.NEEDS_USER_INPUT
            and not any(item.required for item in self.missing_inputs)
        ):
            raise AgentResultContractError(
                "NEEDS_USER_INPUT requires at least one required input"
            )
        if self.status is AgentResultStatus.NEEDS_EVIDENCE and not self.requested_evidence:
            raise AgentResultContractError("NEEDS_EVIDENCE requires evidence requests")
        if self.status is AgentResultStatus.SUCCEEDED and (
            self.missing_inputs or self.requested_evidence
        ):
            raise AgentResultContractError("SUCCEEDED cannot remain blocked on inputs")
        if self.status is AgentResultStatus.RETRYABLE_FAILURE and not self.retryable:
            raise AgentResultContractError("retryable failure must declare retryable")
        if self.retryable and self.status is not AgentResultStatus.RETRYABLE_FAILURE:
            raise AgentResultContractError("only RETRYABLE_FAILURE may be retried")


@dataclass(frozen=True)
class RequestedField:
    field_name: str
    target_work_item_id: str
    value_schema: str
    question_hint: str | None = None

    def __post_init__(self) -> None:
        _required(self.field_name, self.target_work_item_id, self.value_schema)
        if self.question_hint is not None:
            _required(self.question_hint)


@dataclass(frozen=True)
class InteractionRequest:
    interaction_id: str
    requested_fields: tuple[RequestedField, ...]
    expected_state_version: int

    def __post_init__(self) -> None:
        _required(self.interaction_id)
        if not self.requested_fields:
            raise AgentResultContractError("interaction requires requested fields")
        identities = tuple(
            (item.target_work_item_id, item.field_name)
            for item in self.requested_fields
        )
        if len(identities) != len(set(identities)):
            raise AgentResultContractError("interaction fields must be unique per work item")
        if self.expected_state_version < 0:
            raise AgentResultContractError("interaction state version must be non-negative")


def _required(*values: object) -> None:
    if any(not str(value or "").strip() for value in values):
        raise AgentResultContractError("required result field is blank")


def _unique_nonblank(values: tuple[str, ...], label: str) -> None:
    if any(not value.strip() for value in values):
        raise AgentResultContractError(f"{label} must not contain blanks")
    if len(values) != len(set(values)):
        raise AgentResultContractError(f"{label} must be unique")


def _canonical_json(value_json: str, label: str) -> None:
    try:
        value = json.loads(value_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentResultContractError(f"{label} must be JSON") from exc
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if canonical != value_json:
        raise AgentResultContractError(f"{label} must use canonical JSON")
