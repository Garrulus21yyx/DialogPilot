"""Executable work contracts independent of any particular graph engine."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from application.capability_registry import CapabilityEffect, CapabilityRisk


class WorkItemContractError(ValueError):
    pass


class ControlMode(str, Enum):
    DIRECT = "DIRECT"
    DELEGATED = "DELEGATED"
    WORKFLOW = "WORKFLOW"


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
    reconciliation_policy: str | None = None
    aggregate_ref: str | None = None

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
        _unique((item.name for item in self.arguments), "arguments")
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
            self.reconciliation_policy,
            self.aggregate_ref,
        )
        if self.effect is CapabilityEffect.WRITE:
            if self.control_mode is not ControlMode.WORKFLOW:
                raise WorkItemContractError("business writes require WORKFLOW control")
            if any(not str(value or "").strip() for value in write_fields):
                raise WorkItemContractError("write work lacks execution safety bindings")
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
            "arguments": [(item.name, item.value_json) for item in self.arguments],
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
            "reconciliation_policy": self.reconciliation_policy,
            "aggregate_ref": self.aggregate_ref,
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "work-item:v1:" + hashlib.sha256(raw).hexdigest()


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

