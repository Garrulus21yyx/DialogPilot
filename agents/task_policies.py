"""Versioned Agent-owned task formation, execution and synthesis policies."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable, Sequence

from agents.orchestration_contracts import TaskEffect, TaskGraph, TaskSpec


class TaskPolicyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TaskFormationPolicy:
    """Coalesce only tasks that share every execution isolation boundary."""

    version: str = "task-formation-v1"
    max_requirements_per_task: int = 8

    @property
    def fingerprint(self) -> str:
        return _hash(self.__dict__)

    def form(
        self,
        tasks: Sequence[TaskSpec],
        *,
        primary_task_id: str,
        reason: str = "",
        confidence: float = 0.0,
        execution_policy_version: str,
        synthesis_policy_version: str,
        pinned_config_ref: str,
    ) -> TaskGraph:
        if not tasks:
            raise TaskPolicyError("EMPTY_PLAN", "task formation requires proposals")
        if len({task.task_id for task in tasks}) != len(tasks):
            raise TaskPolicyError("DUPLICATE_TASK_ID", "proposal IDs must be unique")
        primary_owner = next(
            (task.owner for task in tasks if task.task_id == primary_task_id), None,
        )
        if primary_owner is None:
            raise TaskPolicyError("PRIMARY_TASK_UNKNOWN", "primary proposal is missing")
        groups: list[list[TaskSpec]] = []
        for task in tasks:
            compatible = next((group for group in groups if self._compatible(group[0], task)), None)
            if compatible is None:
                groups.append([task])
            elif sum(max(1, len(item.requirement_ids)) for item in compatible) + max(
                1, len(task.requirement_ids)
            ) <= self.max_requirements_per_task:
                compatible.append(task)
            else:
                groups.append([replace(task, split_reasons=tuple(dict.fromkeys((
                    *task.split_reasons, "REACT_BUDGET_BOUNDARY",
                ))))])
        formed = tuple(self._merge(group) for group in groups)
        formed_primary = next(
            task.task_id for task, group in zip(formed, groups, strict=True)
            if any(item.task_id == primary_task_id for item in group)
        )
        return TaskGraph(
            formed, formed_primary, reason, confidence,
            formation_policy_version=self.version,
            execution_policy_version=execution_policy_version,
            synthesis_policy_version=synthesis_policy_version,
            pinned_config_ref=pinned_config_ref,
        )

    @staticmethod
    def _compatible(left: TaskSpec, right: TaskSpec) -> bool:
        return (
            left.owner is right.owner
            and left.risk is right.risk
            and left.effect is right.effect is TaskEffect.READ_ONLY
            and left.permission_scope == right.permission_scope
            and left.interrupt_boundary == right.interrupt_boundary
            and left.may_interrupt is right.may_interrupt is False
            and left.depends_on == right.depends_on == ()
            and left.context_refs == right.context_refs
        )

    @staticmethod
    def _merge(group: Sequence[TaskSpec]) -> TaskSpec:
        first = group[0]
        if len(group) == 1:
            return first
        return replace(
            first,
            task_id=f"{first.owner.value}_task",
            objective="\n".join(dict.fromkeys(item.objective for item in group)),
            required=any(item.required for item in group),
            success_criteria=tuple(dict.fromkeys(
                criterion for item in group for criterion in item.success_criteria
            )),
            evidence_spans=tuple(dict.fromkeys(
                span for item in group for span in item.evidence_spans
            )),
            requirement_ids=tuple(dict.fromkeys(
                requirement for item in group for requirement in item.requirement_ids
            )),
            split_reasons=(),
            deterministic_assembly=all(item.deterministic_assembly for item in group),
        )


@dataclass(frozen=True)
class ExecutionSelection:
    selected: tuple[TaskSpec, ...]
    deferred: tuple[TaskSpec, ...]


@dataclass(frozen=True)
class MultiAgentExecutionPolicy:
    version: str = "multi-agent-execution-v1"
    max_planned_tasks: int = 4
    max_executed_tasks_per_request: int = 3
    max_parallel_workers: int = 3
    request_timeout_s: float = 20.0
    worker_timeout_s: float = 15.0
    worker_react_steps: int = 4

    def __post_init__(self) -> None:
        if not 1 <= self.max_executed_tasks_per_request <= self.max_planned_tasks:
            raise ValueError("executed task budget must fit the planned task budget")
        if not 1 <= self.max_parallel_workers <= self.max_executed_tasks_per_request:
            raise ValueError("parallel worker budget must fit the execution budget")
        if min(self.request_timeout_s, self.worker_timeout_s) <= 0:
            raise ValueError("execution timeouts must be positive")
        if self.worker_react_steps < 1:
            raise ValueError("worker ReAct budget must be positive")

    @property
    def fingerprint(self) -> str:
        return _hash(self.__dict__)

    def select(self, plan: TaskGraph) -> ExecutionSelection:
        if len(plan.tasks) > self.max_planned_tasks:
            raise TaskPolicyError(
                "PLAN_TOO_LARGE",
                f"plan has {len(plan.tasks)} tasks; maximum is {self.max_planned_tasks}",
            )
        ordered = plan.topological_tasks
        selected = ordered[:self.max_executed_tasks_per_request]
        selected_ids = {task.task_id for task in selected}
        if any(
            dependency not in selected_ids
            for task in selected for dependency in task.depends_on
        ):
            raise TaskPolicyError(
                "SELECTION_NOT_DEPENDENCY_CLOSED",
                "execution selection omitted an upstream dependency",
            )
        return ExecutionSelection(selected, ordered[len(selected):])


class SynthesisMode(str, Enum):
    NONE = "NONE"
    DIRECT = "DIRECT"
    DETERMINISTIC = "DETERMINISTIC"
    LLM = "LLM"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class SynthesisInvocationPolicy:
    version: str = "synthesis-invocation-v1"

    @property
    def fingerprint(self) -> str:
        return _hash(self.__dict__)

    def decide(
        self,
        plan: TaskGraph,
        *,
        successful_task_ids: Iterable[str],
        coverage_complete: bool,
        authority_conflicts: Sequence[str] = (),
    ) -> SynthesisMode:
        successful = tuple(dict.fromkeys(successful_task_ids))
        if authority_conflicts:
            return SynthesisMode.CONFLICT
        if not successful or not coverage_complete:
            return SynthesisMode.NONE
        if len(successful) == 1:
            return SynthesisMode.DIRECT
        tasks = {task.task_id: task for task in plan.tasks}
        if any(task_id not in tasks for task_id in successful):
            raise TaskPolicyError("OUTCOME_TASK_UNKNOWN", "outcome is outside the plan")
        if all(tasks[task_id].deterministic_assembly for task_id in successful):
            return SynthesisMode.DETERMINISTIC
        return SynthesisMode.LLM


def pinned_config_ref(
    formation: TaskFormationPolicy,
    execution: MultiAgentExecutionPolicy,
    synthesis: SynthesisInvocationPolicy,
) -> str:
    return "task-config-v1:" + _hash({
        "formation": formation.fingerprint,
        "execution": execution.fingerprint,
        "synthesis": synthesis.fingerprint,
    })
