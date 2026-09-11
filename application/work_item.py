"""Executable work contracts independent of any particular graph engine."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
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


def prerequisite_ids(item: WorkItem, items: Iterable[WorkItem]) -> tuple[str, ...]:
    """Resolve provenance through current and retained work, without guessing gaps."""
    by_id = {}
    for work in items:
        prior = by_id.setdefault(work.work_item_id, work)
        if prior != work:
            raise WorkItemContractError("prerequisite identity has conflicting contracts")
    pending, seen = list(item.dependencies), set()
    while pending:
        identity = pending.pop()
        if identity not in by_id:
            raise WorkItemContractError("prerequisite provenance is incomplete")
        if identity not in seen:
            seen.add(identity)
            pending.extend(by_id[identity].dependencies)
    from graphlib import TopologicalSorter, CycleError
    try:
        tuple(TopologicalSorter({key: by_id[key].dependencies for key in seen}).static_order())
    except CycleError as exc:
        raise WorkItemContractError("prerequisite provenance contains a cycle") from exc
    return tuple(sorted(seen))


def planning_continuations(state, resolved_items=()):
    """Only persisted waits or resolver-accepted envelopes authorize continuation."""
    items = {(item.work_item_id, item.control): item
        for pending in (state.pending_interaction, state.pending_approval)
        if pending is not None for item in pending.suspended_work_items}
    items.update({(item.work_item_id, item.control): item for item in resolved_items})
    return tuple(item for item in items.values()
                 if item.control is not None and state.accepts(item.control))


class ControlMode(str, Enum):
    DIRECT = "DIRECT"
    DELEGATED = "DELEGATED"
    WORKFLOW = "WORKFLOW"
    ACTION = "ACTION"


class DependencySatisfaction(str, Enum):
    SUCCESS_WITH_COVERAGE = "SUCCESS_WITH_COVERAGE"


class ActionSerialization(str, Enum):
    NONE = "NONE"
    ONE_PENDING_ACTION_PER_CONVERSATION = "ONE_PENDING_ACTION_PER_CONVERSATION"


class MissingDependencyOutcome(str, Enum):
    BLOCK = "BLOCK"


class RetainedOutcomeScope(str, Enum):
    CONTROL_REVISION = "CONTROL_REVISION"


class PartialDeliveryPolicy(str, Enum):
    DELIVERABLE_OUTCOMES_ONLY = "DELIVERABLE_OUTCOMES_ONLY"


@dataclass(frozen=True)
class WorkPlanPolicy:
    """Execution contract fixed by the compiler, not selected by the model."""

    dependency_satisfaction: DependencySatisfaction = DependencySatisfaction.SUCCESS_WITH_COVERAGE
    action_serialization: ActionSerialization = ActionSerialization.ONE_PENDING_ACTION_PER_CONVERSATION
    missing_dependency_outcome: MissingDependencyOutcome = MissingDependencyOutcome.BLOCK
    retained_outcome_scope: RetainedOutcomeScope = RetainedOutcomeScope.CONTROL_REVISION
    partial_delivery: PartialDeliveryPolicy = PartialDeliveryPolicy.DELIVERABLE_OUTCOMES_ONLY

    @classmethod
    def default(cls) -> "WorkPlanPolicy":
        return cls()

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependency_satisfaction", DependencySatisfaction(self.dependency_satisfaction))
        object.__setattr__(self, "action_serialization", ActionSerialization(self.action_serialization))
        object.__setattr__(self, "missing_dependency_outcome", MissingDependencyOutcome(self.missing_dependency_outcome))
        object.__setattr__(self, "retained_outcome_scope", RetainedOutcomeScope(self.retained_outcome_scope))
        object.__setattr__(self, "partial_delivery", PartialDeliveryPolicy(self.partial_delivery))

    @property
    def fingerprint(self) -> str:
        payload = {
            "dependency_satisfaction": self.dependency_satisfaction.value,
            "action_serialization": self.action_serialization.value,
            "missing_dependency_outcome": self.missing_dependency_outcome.value,
            "retained_outcome_scope": self.retained_outcome_scope.value,
            "partial_delivery": self.partial_delivery.value,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "work-plan-policy:v1:" + hashlib.sha256(raw).hexdigest()


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
    continuation_of: str | None = None
    # Host-owned return-to-conversation obligation; preserved through suspension.
    observe_result: bool = False

    def __post_init__(self) -> None:
        if type(self.observe_result) is not bool or (self.observe_result and (
                self.control_mode is not ControlMode.DIRECT or self.effect is not CapabilityEffect.READ)):
            raise WorkItemContractError("conversation observation requires direct read work")
        # Checkpoint codecs accept JSON-style sequences; the immutable contract
        # has one representation regardless of whether it was freshly compiled.
        for name in ("allowed_tools", "allowed_skills", "arguments", "requirement_ids",
                     "dependencies", "argument_bindings", "allowed_actions"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
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
        if self.continuation_of is not None and (
                not self.continuation_of.strip() or self.control is None
                or self.control.revision < 2 or self.effect is not CapabilityEffect.READ):
            raise WorkItemContractError("continuation requires a versioned read objective")
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
            "continuation_of": self.continuation_of,
            "observe_result": self.observe_result,
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
                self.reconciliation.to_payload()
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
                self.reconciliation.to_payload()
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
    policy: WorkPlanPolicy = field(default_factory=WorkPlanPolicy.default)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "policy", _coerce_work_plan_policy(self.policy))
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

    @property
    def fingerprint(self) -> str:
        raw = json.dumps(
            {
                "primary": self.primary_work_item_id,
                "items": [item.fingerprint for item in self.items],
                "policy": self.policy.fingerprint,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "work-plan:v1:" + hashlib.sha256(raw).hexdigest()

    def unreplaced_outcomes(
        self,
        previous: tuple[tuple[WorkItem, object], ...],
    ) -> tuple[tuple[WorkItem, object], ...]:
        if self.policy.retained_outcome_scope is not RetainedOutcomeScope.CONTROL_REVISION:
            raise WorkItemContractError("unsupported retained outcome scope")
        return tuple((item, result) for item, result in previous
            if not any(item == new or (
                item.control and new.control and item.control.control_id == new.control.control_id
                and item.control.revision < new.control.revision) for new in self.items))

    def reassignment_sources(self, item, previous):
        """Investigation inputs of an explicitly revised failed assignment.

        A repaired task may change owner; it does not resume the old worker or
        inherit its approval. These sources are inputs, not completion outcomes.
        """
        from application.agent_result import AgentResultStatus
        if item not in self.items:
            raise WorkItemContractError("reassignment input requires a plan member")
        if item.control is None or item.continuation_of is not None:
            return ()
        return tuple(result for original, result in previous
            if result is not None and result.assignment_issue
            and result.status is AgentResultStatus.TERMINAL_FAILURE
            and original.effect is CapabilityEffect.READ
            and original.control is not None
            and original.control.control_id == item.control.control_id
            and original.control.revision + 1 == item.control.revision
            and original.registry_fingerprint == item.registry_fingerprint
            and result.work_item_id == original.work_item_id
            and result.owner_agent == original.owner_agent)

    def dependency_closure(self, item: WorkItem) -> tuple[str, ...]:
        """All prerequisite identities, including already completed ancestors."""
        if item not in self.items:
            raise WorkItemContractError("dependency closure requires a plan member")
        return prerequisite_ids(item, self.items)

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


def _coerce_work_plan_policy(value: object) -> WorkPlanPolicy:
    if isinstance(value, WorkPlanPolicy):
        return WorkPlanPolicy(
            value.dependency_satisfaction,
            value.action_serialization,
            value.missing_dependency_outcome,
            value.retained_outcome_scope,
            value.partial_delivery,
        )
    raise WorkItemContractError("work plan policy is invalid")
