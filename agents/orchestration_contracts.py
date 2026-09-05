"""Legacy TaskGraph contracts still consumed by planning/evaluation modules.

Current Target execution owns WorkPlan, AgentResult and ResultBoard in application.
This module does not own Target result coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import time
from typing import Any, Dict, Iterable, Tuple


class AgentType(str, Enum):
    """当前能力注册表支持的 Agent Owner。"""

    GENERAL = "general"
    TECHNICAL = "technical"
    BILLING = "billing"
    ACCOUNT_SECURITY = "account_security"
    ESCALATION = "escalation"


class TaskRisk(str, Enum):
    """子任务风险等级；高风险任务必须经过发布校验或人工接管。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskEffect(str, Enum):
    """计划阶段的副作用上界，由调度器决定是否可并行。"""

    READ_ONLY = "read_only"
    WRITE_REQUIRES_APPROVAL = "write_requires_approval"


class PendingSignalKind(str, Enum):
    """A non-terminal external input awaited by the current task."""

    USER_INPUT = "user_input"
    APPROVAL = "approval"
    HUMAN_RESULT = "human_result"
    MEDIA_RESOLVED = "media_resolved"
    RECONCILIATION_RESULT = "reconciliation_result"


@dataclass(frozen=True)
class PendingSignal:
    """Native interrupt fact; it must never be stored as a terminal task outcome."""

    kind: PendingSignalKind
    signal_id: str
    task_id: str
    version: str = "pending-signal-v1"
    workflow_run_id: str = ""
    payload_schema_version: str = "pending-signal-payload-v1"

    def __post_init__(self) -> None:
        if any(not str(value).strip() for value in (
            self.signal_id,
            self.task_id,
            self.version,
            self.payload_schema_version,
        )):
            raise ValueError("pending signal identity/version is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind.value,
            "signal_id": self.signal_id,
            "task_id": self.task_id,
            "version": self.version,
            "workflow_run_id": self.workflow_run_id,
            "payload_schema_version": self.payload_schema_version,
        }


@dataclass(frozen=True)
class DependencyInput:
    """Typed upstream artifact that a dependent task must actually consume."""

    upstream_task_id: str
    artifact_kind: str
    receipt_schema: str

    def __post_init__(self) -> None:
        if any(not str(value).strip() for value in (
            self.upstream_task_id, self.artifact_kind, self.receipt_schema,
        )):
            raise ValueError("dependency input identity is required")


@dataclass(frozen=True)
class PriorOutcomeBinding:
    """Read-only prior outcome input for a future dependency-closed delta plan."""

    task_id: str
    requirement_ids: Tuple[str, ...]
    artifact_refs: Tuple[str, ...]
    evidence_receipt_refs: Tuple[str, ...]
    producer_version: str

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.producer_version.strip():
            raise ValueError("prior outcome identity/version is required")
        if not self.requirement_ids:
            raise ValueError("prior outcome must bind requirements")
        if any(not item.strip() for item in (
            *self.requirement_ids, *self.artifact_refs, *self.evidence_receipt_refs,
        )):
            raise ValueError("prior outcome references must not be blank")


@dataclass(frozen=True)
class TaskArtifact:
    """Typed in-memory task product; durable meaning stays with its receipt Owner."""

    artifact_ref: str
    artifact_kind: str
    schema_version: str
    content: str
    evidence_receipt_refs: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if any(not str(value).strip() for value in (
            self.artifact_ref, self.artifact_kind, self.schema_version,
        )):
            raise ValueError("task artifact identity is required")
        if any(not item.strip() for item in self.evidence_receipt_refs):
            raise ValueError("evidence receipt refs must not be blank")


@dataclass(frozen=True)
class TaskSpec:
    """一个有 Owner、证据、上下文范围和依赖的可验收子任务。"""

    task_id: str
    owner: AgentType
    objective: str
    required: bool = True
    risk: TaskRisk = TaskRisk.LOW
    success_criteria: Tuple[str, ...] = field(default_factory=tuple)
    evidence_spans: Tuple[str, ...] = field(default_factory=tuple)
    context_refs: Tuple[str, ...] = field(default_factory=tuple)
    depends_on: Tuple[str, ...] = field(default_factory=tuple)
    effect: TaskEffect = TaskEffect.READ_ONLY
    requirement_ids: Tuple[str, ...] = field(default_factory=tuple)
    dependency_inputs: Tuple[DependencyInput, ...] = field(default_factory=tuple)
    permission_scope: str = "authenticated_user"
    interrupt_boundary: str = "request"
    may_interrupt: bool = False
    split_reasons: Tuple[str, ...] = field(default_factory=tuple)
    deterministic_assembly: bool = False

    def __post_init__(self) -> None:
        """拒绝无法追踪或无法执行的空任务。"""
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.objective.strip():
            raise ValueError("task objective must not be empty")
        if self.task_id in self.depends_on:
            raise ValueError("task cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("task dependencies must be unique")
        if any(not str(item).strip() for item in self.depends_on):
            raise ValueError("task dependencies must not be empty")
        if any(not str(item).strip() for item in self.requirement_ids):
            raise ValueError("task requirements must not be empty")
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("task requirements must be unique")
        if not self.permission_scope.strip() or not self.interrupt_boundary.strip():
            raise ValueError("task permission/interrupt boundary is required")
        if len(self.dependency_inputs) != len(set(self.dependency_inputs)):
            raise ValueError("dependency inputs must be unique")
        undeclared = {
            item.upstream_task_id for item in self.dependency_inputs
            if item.upstream_task_id not in self.depends_on
        }
        if undeclared:
            raise ValueError(
                f"dependency inputs reference undeclared tasks: {sorted(undeclared)}"
            )

    @property
    def scoped_input(self) -> str:
        """工作者只消费本任务证据；无切片时由 Planner 显式放入 objective。"""
        return "\n".join(span for span in self.evidence_spans if span.strip()) or self.objective

    def to_dict(self) -> Dict[str, Any]:
        """生成稳定的 API/Trace 投影。"""
        data = asdict(self)
        data["owner"] = self.owner.value
        data["risk"] = self.risk.value
        data["effect"] = self.effect.value
        data["success_criteria"] = list(self.success_criteria)
        data["evidence_spans"] = list(self.evidence_spans)
        data["context_refs"] = list(self.context_refs)
        data["depends_on"] = list(self.depends_on)
        data["requirement_ids"] = list(self.requirement_ids)
        data["dependency_inputs"] = [asdict(item) for item in self.dependency_inputs]
        data["split_reasons"] = list(self.split_reasons)
        return data


@dataclass(frozen=True)
class TaskGraph:
    """一次请求唯一的 DAG、Owner 分配和执行语义事实。"""

    tasks: Tuple[TaskSpec, ...]
    primary_task_id: str
    reason: str = ""
    confidence: float = 0.0
    formation_policy_version: str = "legacy-task-formation-v1"
    execution_policy_version: str = "legacy-multi-agent-execution-v1"
    synthesis_policy_version: str = "legacy-synthesis-invocation-v1"
    pinned_config_ref: str = "legacy-unpinned"

    def __post_init__(self) -> None:
        """在执行前关闭重复 ID、悬空依赖和有向环。"""
        if not self.tasks:
            raise ValueError("task plan must contain at least one task")
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task plan contains duplicate task ids")
        if self.primary_task_id not in set(task_ids):
            raise ValueError("primary_task_id is not present in task plan")
        task_id_set = set(task_ids)
        dangling = sorted({
            dependency
            for task in self.tasks
            for dependency in task.depends_on
            if dependency not in task_id_set
        })
        if dangling:
            raise ValueError(f"task graph contains dangling dependencies: {dangling}")
        # 执行一次拓扑分层，在调用任何 Worker 前拒绝环。
        self.execution_waves()
        if any(not value.strip() for value in (
            self.formation_policy_version, self.execution_policy_version,
            self.synthesis_policy_version, self.pinned_config_ref,
        )):
            raise ValueError("task graph policy versions must be pinned")
        if self.formation_policy_version != "legacy-task-formation-v1":
            missing_inputs = {
                dependency
                for task in self.tasks
                for dependency in task.depends_on
                if not any(
                    item.upstream_task_id == dependency
                    for item in task.dependency_inputs
                )
            }
            if missing_inputs:
                raise ValueError(
                    f"versioned task dependencies lack typed inputs: {sorted(missing_inputs)}"
                )

    @property
    def primary_task(self) -> TaskSpec:
        """返回用户主诉对应的任务。"""
        return next(task for task in self.tasks if task.task_id == self.primary_task_id)

    @property
    def primary_agent(self) -> AgentType:
        """兼容 API 投影：主任务 Owner 即主 Agent。"""
        return self.primary_task.owner

    @property
    def supporting_agents(self) -> list[AgentType]:
        """按任务顺序返回去重后的辅助 Owner。"""
        return list(dict.fromkeys(
            task.owner for task in self.tasks
            if task.task_id != self.primary_task_id
        ))

    @property
    def agent_types(self) -> list[AgentType]:
        """按主任务优先顺序返回本次涉及的 Owner。"""
        return list(dict.fromkeys(task.owner for task in self.ordered_tasks))

    @property
    def ordered_tasks(self) -> Tuple[TaskSpec, ...]:
        """主任务在前，其余任务保持规划顺序。"""
        return (self.primary_task,) + tuple(
            task for task in self.tasks if task.task_id != self.primary_task_id
        )

    @property
    def multi_agent(self) -> bool:
        """只有多个不同 Owner 时才属于多 Agent fan-out。"""
        return len(self.agent_types) > 1

    @property
    def fingerprint(self) -> str:
        """Bind immutable task order, dependencies and all execution policies."""
        payload = {
            "tasks": [task.to_dict() for task in self.tasks],
            "primary_task_id": self.primary_task_id,
            "reason": self.reason, "confidence": self.confidence,
            "formation_policy_version": self.formation_policy_version,
            "execution_policy_version": self.execution_policy_version,
            "synthesis_policy_version": self.synthesis_policy_version,
            "pinned_config_ref": self.pinned_config_ref,
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def execution_waves(
        self,
        task_ids: Iterable[str] | None = None,
    ) -> Tuple[Tuple[TaskSpec, ...], ...]:
        """生成稳定的拓扑波次；同波次任务在依赖代数上可并行。"""
        selected = set(task_ids) if task_ids is not None else {task.task_id for task in self.tasks}
        unknown = selected - {task.task_id for task in self.tasks}
        if unknown:
            raise ValueError(f"unknown task ids requested: {sorted(unknown)}")
        remaining = [task for task in self.tasks if task.task_id in selected]
        omitted_dependencies = {
            dependency
            for task in remaining
            for dependency in task.depends_on
            if dependency not in selected
        }
        if omitted_dependencies:
            raise ValueError(
                f"task selection omits dependencies: {sorted(omitted_dependencies)}"
            )
        completed: set[str] = set()
        waves = []
        while remaining:
            ready = tuple(
                task for task in remaining
                if set(task.depends_on).issubset(completed)
            )
            if not ready:
                cycle_ids = [task.task_id for task in remaining]
                raise ValueError(f"task graph contains a dependency cycle: {cycle_ids}")
            waves.append(ready)
            ready_ids = {task.task_id for task in ready}
            completed.update(ready_ids)
            remaining = [task for task in remaining if task.task_id not in ready_ids]
        return tuple(waves)

    @property
    def topological_tasks(self) -> Tuple[TaskSpec, ...]:
        """按波次展平的稳定执行顺序。"""
        return tuple(task for wave in self.execution_waves() for task in wave)

    def to_dict(self) -> Dict[str, Any]:
        """生成可诊断且不重复建立权威的计划投影。"""
        return {
            "graph_version": 2,
            "plan_fingerprint": self.fingerprint,
            "formation_policy_version": self.formation_policy_version,
            "execution_policy_version": self.execution_policy_version,
            "synthesis_policy_version": self.synthesis_policy_version,
            "pinned_config_ref": self.pinned_config_ref,
            "primary_task_id": self.primary_task_id,
            "reason": self.reason,
            "confidence": self.confidence,
            "tasks": [task.to_dict() for task in self.ordered_tasks],
            "execution_waves": [
                [task.task_id for task in wave]
                for wave in self.execution_waves()
            ],
        }


# 向后兼容旧的内部导入名；权威类型已是 TaskGraph。
TaskPlan = TaskGraph




@dataclass(frozen=True)
class ExecutionBudget:
    """一次编排请求允许使用的时间和并发 Agent 上限。"""

    request_timeout_s: float = 20.0
    agent_timeout_s: float = 15.0
    max_agents: int = 3
    max_planned_tasks: int = 4
    max_parallel_workers: int = 0
    worker_react_steps: int = 4

    def __post_init__(self) -> None:
        """配置错误应在启动时失败，不能退化成随机运行时行为。"""
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")
        if self.agent_timeout_s <= 0:
            raise ValueError("agent_timeout_s must be positive")
        if self.max_agents < 1:
            raise ValueError("max_agents must be at least one")
        if self.max_planned_tasks < self.max_agents:
            raise ValueError("max_planned_tasks must cover max_agents")
        if self.max_parallel_workers == 0:
            object.__setattr__(self, "max_parallel_workers", min(3, self.max_agents))
        if not 1 <= self.max_parallel_workers <= self.max_agents:
            raise ValueError("max_parallel_workers must fit max_agents")
        if self.worker_react_steps < 1:
            raise ValueError("worker_react_steps must be at least one")

    def start(self) -> "ExecutionWindow":
        """为一次请求创建使用单调时钟的运行窗口。"""
        return ExecutionWindow(
            budget=self,
            started_at=time.monotonic(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """暴露配置而不暴露进程局部的单调时钟值。"""
        return {
            "request_timeout_s": self.request_timeout_s,
            "agent_timeout_s": self.agent_timeout_s,
            "max_agents": self.max_agents,
            "max_executed_tasks_per_request": self.max_agents,
            "max_planned_tasks": self.max_planned_tasks,
            "max_parallel_workers": self.max_parallel_workers,
            "worker_react_steps": self.worker_react_steps,
        }


@dataclass(frozen=True)
class ExecutionWindow:
    """多个并行 Worker 共享的请求级 deadline。"""

    budget: ExecutionBudget
    started_at: float

    @property
    def deadline(self) -> float:
        """返回单调时钟上的绝对截止点。"""
        return self.started_at + self.budget.request_timeout_s

    def remaining_s(self) -> float:
        """返回非负剩余时间，供 Worker、融合器和降级路径共同消费。"""
        return max(0.0, self.deadline - time.monotonic())

    def agent_timeout(self) -> float:
        """单 Worker 只能使用 Agent 上限和请求剩余时间中的较小值。"""
        return min(self.budget.agent_timeout_s, self.remaining_s())
