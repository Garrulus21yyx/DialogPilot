"""Executable work contracts independent of any particular graph engine."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from application.capability_registry import (
    ActionReconciliationDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRisk,
)
from application.entity_binding import EntityBinding


class WorkItemContractError(ValueError):
    pass


class ControlMode(str, Enum):
    DIRECT = "DIRECT"
    DELEGATED = "DELEGATED"
    WORKFLOW = "WORKFLOW"
    ACTION = "ACTION"


@dataclass(frozen=True)
class WorkControlBinding:
    """Versioned authority binding for one independently steerable objective."""

    control_id: str
    revision: int

    def __post_init__(self) -> None:
        if not self.control_id.strip() or self.revision < 1:
            raise WorkItemContractError("work control binding is invalid")


@dataclass(frozen=True)
class ArgumentValue:
    name: str
    value_json: str

    @classmethod
    def create(cls, name: str, value: object) -> "ArgumentValue":
        return cls(name, _canonical_json(value))

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise WorkItemContractError("argument name is required")
        try:
            value = json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WorkItemContractError("argument value must be JSON") from exc
        if _canonical_json(value) != self.value_json:
            raise WorkItemContractError("argument value must use canonical JSON")

    @property
    def value(self) -> object:
        return json.loads(self.value_json)


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    owner_agent: str
    objective: str
    control_mode: ControlMode
    allowed_tools: tuple[str, ...]
    allowed_skills: tuple[str, ...]
    arguments: tuple[ArgumentValue, ...]
    requirement_ids: tuple[str, ...]
    dependencies: tuple[str, ...]
    effect: CapabilityEffect
    risk: CapabilityRisk
    expected_output_schema: str
    verification_profile: str
    state_snapshot_version: int
    registry_fingerprint: str
    timeout_seconds: float
    max_steps: int
    skill_hint: str | None = None
    flow_ref: str | None = None
    operation_key: str | None = None
    approval_binding: str | None = None
    target_entity_version: str | None = None
    reconciliation: ActionReconciliationDefinition | None = None
    aggregate_ref: str | None = None
    action_ref: str | None = None
    approval_policy: ApprovalPolicy | None = None
    argument_bindings: tuple[EntityBinding, ...] = ()
    control: WorkControlBinding | None = None
    allowed_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        required = (
            self.work_item_id,
            self.owner_agent,
            self.objective,
            self.expected_output_schema,
            self.verification_profile,
            self.registry_fingerprint,
        )
        if any(not str(value or "").strip() for value in required):
            raise WorkItemContractError("work item identity and contract are required")
        _unique(self.allowed_tools, "allowed tools")
        _unique(self.allowed_skills, "allowed skills")
        _unique(self.allowed_actions, "allowed actions")
        if self.allowed_actions and self.control_mode is not ControlMode.DELEGATED:
            raise WorkItemContractError("action proposals belong to delegated work")
        _unique((item.name for item in self.arguments), "arguments")
        _unique((item.field_name for item in self.argument_bindings), "argument bindings")
        arguments = {item.name: item.value_json for item in self.arguments}
        if any(
            item.field_name not in arguments
            or arguments[item.field_name] != item.value_json
            for item in self.argument_bindings
        ):
            raise WorkItemContractError("argument binding does not match its argument")
        _unique(self.requirement_ids, "requirements")
        _unique(self.dependencies, "dependencies")
        if self.work_item_id in self.dependencies:
            raise WorkItemContractError("work item cannot depend on itself")
        if self.state_snapshot_version < 0:
            raise WorkItemContractError("state snapshot version must be non-negative")
        if self.timeout_seconds <= 0 or self.max_steps < 1:
            raise WorkItemContractError("work item execution budget must be positive")

        if self.control_mode is ControlMode.DIRECT:
            if not self.allowed_tools or self.allowed_skills:
                raise WorkItemContractError("DIRECT requires tools and forbids skills")
            if self.skill_hint is not None or self.flow_ref is not None:
                raise WorkItemContractError("DIRECT cannot carry skill or flow hints")
        elif self.control_mode is ControlMode.DELEGATED:
            if not self.allowed_tools and not self.allowed_skills:
                raise WorkItemContractError("DELEGATED requires a capability envelope")
            if self.flow_ref is not None:
                raise WorkItemContractError("DELEGATED cannot execute a workflow")
            if self.skill_hint is not None and self.skill_hint not in self.allowed_skills:
                raise WorkItemContractError("skill hint is outside the allowed skills")
        elif self.control_mode is ControlMode.ACTION:
            if self.flow_ref is not None or self.skill_hint is not None:
                raise WorkItemContractError("ACTION has no flow or skill hint")
            if self.effect is not CapabilityEffect.WRITE:
                raise WorkItemContractError("ACTION requires a business write")
        elif self.control_mode is ControlMode.WORKFLOW:
            if not self.flow_ref:
                raise WorkItemContractError("WORKFLOW requires a pinned flow version")
            if self.skill_hint is not None:
                raise WorkItemContractError("WORKFLOW cannot carry a skill hint")
        else:
            raise WorkItemContractError("unsupported work control mode")

        write_fields = (
            self.operation_key,
            self.approval_binding,
            self.target_entity_version,
            self.reconciliation,
            self.aggregate_ref,
            self.action_ref,
            self.approval_policy,
        )
        if self.effect is CapabilityEffect.WRITE:
            if self.control_mode not in {ControlMode.WORKFLOW, ControlMode.ACTION}:
                raise WorkItemContractError("business writes require WORKFLOW control")
            if any(not str(value or "").strip() for value in write_fields):
                raise WorkItemContractError("write work lacks execution safety bindings")
            if self.reconciliation.tool_id not in self.allowed_tools:
                raise WorkItemContractError(
                    "write work excludes its reconciliation tool"
                )
        elif any(value is not None for value in write_fields):
            raise WorkItemContractError("read work cannot carry write-only bindings")

    @property
    def fingerprint(self) -> str:
        payload = {
            "work_item_id": self.work_item_id,
            "owner_agent": self.owner_agent,
            "objective": self.objective,
            "control_mode": self.control_mode.value,
            "allowed_tools": self.allowed_tools,
            "allowed_skills": self.allowed_skills,
            "allowed_actions": self.allowed_actions,
            "arguments": [(item.name, item.value_json) for item in self.arguments],
            "argument_bindings": [
                {
                    "field_name": item.field_name,
                    "value_json": item.value_json,
                    "source": item.source.value,
                    "source_ref": item.source_ref,
                    "scope": (
                        item.tenant_id, item.user_id, item.conversation_id,
                    ),
                    "priority": item.priority,
                    "source_version": item.source_version,
                    "workstream_id": item.workstream_id,
                    "valid_until": item.valid_until,
                    "type_selection": item.type_selection,
                }
                for item in self.argument_bindings
            ],
            "requirements": self.requirement_ids,
            "dependencies": self.dependencies,
            "effect": self.effect.value,
            "risk": self.risk.value,
            "expected_output_schema": self.expected_output_schema,
            "verification_profile": self.verification_profile,
            "state_snapshot_version": self.state_snapshot_version,
            "registry_fingerprint": self.registry_fingerprint,
            "timeout_seconds": self.timeout_seconds,
            "max_steps": self.max_steps,
            "skill_hint": self.skill_hint,
            "flow_ref": self.flow_ref,
            "operation_key": self.operation_key,
            "approval_binding": self.approval_binding,
            "target_entity_version": self.target_entity_version,
            "reconciliation": (
                {
                    "tool_id": self.reconciliation.tool_id,
                    "requirement_id": self.reconciliation.requirement_id,
                    "operation_key_argument": self.reconciliation.operation_key_argument,
                    "operation_key_field": self.reconciliation.operation_key_field,
                    "passthrough_arguments": self.reconciliation.passthrough_arguments,
                    "receipt_id_field": self.reconciliation.receipt_id_field,
                }
                if self.reconciliation else None
            ),
            "aggregate_ref": self.aggregate_ref,
            "action_ref": self.action_ref,
            "approval_policy": (
                self.approval_policy.value if self.approval_policy else None
            ),
            "control": (
                {"control_id": self.control.control_id, "revision": self.control.revision}
                if self.control else None
            ),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "work-item:v1:" + hashlib.sha256(raw).hexdigest()

    @property
    def operation_fingerprint(self) -> str:
        """Stable identity of a governed side effect across turn retries.

        A WorkItem fingerprint identifies one execution attempt and therefore
        includes its snapshot and execution budget.  The operation ledger owns
        a different invariant: one operation key must always denote the same
        business mutation, even when a later turn only reconciles its outcome.
        """
        if self.effect is not CapabilityEffect.WRITE:
            raise WorkItemContractError("only write work has an operation fingerprint")
        payload = {
            "owner_agent": self.owner_agent,
            "control_mode": self.control_mode.value,
            "allowed_tools": self.allowed_tools,
            "allowed_skills": self.allowed_skills,
            "arguments": [(item.name, item.value_json) for item in self.arguments],
            "argument_bindings": [
                (item.field_name, item.value_json, item.source_ref, item.type_selection)
                for item in self.argument_bindings
            ],
            "requirements": self.requirement_ids,
            "effect": self.effect.value,
            "risk": self.risk.value,
            "expected_output_schema": self.expected_output_schema,
            "verification_profile": self.verification_profile,
            "registry_fingerprint": self.registry_fingerprint,
            "flow_ref": self.flow_ref,
            "operation_key": self.operation_key,
            "approval_binding": self.approval_binding,
            "target_entity_version": self.target_entity_version,
            "reconciliation": (
                {
                    "tool_id": self.reconciliation.tool_id,
                    "requirement_id": self.reconciliation.requirement_id,
                    "operation_key_argument": self.reconciliation.operation_key_argument,
                    "operation_key_field": self.reconciliation.operation_key_field,
                    "passthrough_arguments": self.reconciliation.passthrough_arguments,
                    "receipt_id_field": self.reconciliation.receipt_id_field,
                }
                if self.reconciliation else None
            ),
            "aggregate_ref": self.aggregate_ref,
            "action_ref": self.action_ref,
            "approval_policy": (
                self.approval_policy.value if self.approval_policy else None
            ),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "operation-contract:v1:" + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class WorkPlan:
    items: tuple[WorkItem, ...]
    primary_work_item_id: str

    def __post_init__(self) -> None:
        if not self.items:
            raise WorkItemContractError("work plan requires items")
        ids = tuple(item.work_item_id for item in self.items)
        _unique(ids, "work item IDs")
        if self.primary_work_item_id not in ids:
            raise WorkItemContractError("primary work item is not in the plan")
        known = set(ids)
        if any(set(item.dependencies).difference(known) for item in self.items):
            raise WorkItemContractError("work item depends on an unknown item")
        self.execution_waves()

    def execution_waves(
        self,
        selected_ids: Iterable[str] | None = None,
    ) -> tuple[tuple[WorkItem, ...], ...]:
        selected = set(selected_ids) if selected_ids is not None else {
            item.work_item_id for item in self.items
        }
        known = {item.work_item_id for item in self.items}
        if selected.difference(known):
            raise WorkItemContractError("work selection contains unknown items")
        remaining = [item for item in self.items if item.work_item_id in selected]
        if any(set(item.dependencies).difference(selected) for item in remaining):
            raise WorkItemContractError("work selection omits a dependency")
        completed: set[str] = set()
        waves: list[tuple[WorkItem, ...]] = []
        while remaining:
            ready = tuple(
                item for item in remaining
                if set(item.dependencies).issubset(completed)
            )
            if not ready:
                raise WorkItemContractError("work plan contains a dependency cycle")
            waves.append(ready)
            ready_ids = {item.work_item_id for item in ready}
            completed.update(ready_ids)
            remaining = [item for item in remaining if item.work_item_id not in ready_ids]
        return tuple(waves)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _unique(values: object, label: str) -> None:
    materialized = tuple(values)  # type: ignore[arg-type]
    if any(not str(value).strip() for value in materialized):
        raise WorkItemContractError(f"{label} must not contain blank values")
    if len(materialized) != len(set(materialized)):
        raise WorkItemContractError(f"{label} must be unique")
