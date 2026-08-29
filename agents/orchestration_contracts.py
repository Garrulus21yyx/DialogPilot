"""多 Agent 任务计划、执行预算和覆盖结果的权威合同。

本模块只定义编排事实，不执行模型调用。路由器生产 ``TaskPlan``，执行器为每个
``TaskSpec`` 生产一个闭合 outcome，CoverageGate 再把这些事实投影为
``CoverageReport``。这样“选了哪些 Agent”不再冒充“用户的哪些问题已解决”。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import time
from typing import Any, Dict, Tuple


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


@dataclass(frozen=True)
class TaskSpec:
    """一个可独立派发、可独立验收的子任务。"""

    task_id: str
    owner: AgentType
    instruction: str
    required: bool = True
    risk: TaskRisk = TaskRisk.LOW
    success_criteria: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """拒绝无法追踪或无法执行的空任务。"""
        if not self.task_id.strip():
            raise ValueError("task_id must not be empty")
        if not self.instruction.strip():
            raise ValueError("task instruction must not be empty")

    def to_dict(self) -> Dict[str, Any]:
        """生成稳定的 API/Trace 投影。"""
        data = asdict(self)
        data["owner"] = self.owner.value
        data["risk"] = self.risk.value
        data["success_criteria"] = list(self.success_criteria)
        return data


@dataclass(frozen=True)
class TaskPlan:
    """一次请求唯一的任务拆分和 Owner 分配事实。"""

    tasks: Tuple[TaskSpec, ...]
    primary_task_id: str
    reason: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        """在执行前关闭重复 ID、空计划和悬空主任务。"""
        if not self.tasks:
            raise ValueError("task plan must contain at least one task")
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task plan contains duplicate task ids")
        if self.primary_task_id not in set(task_ids):
            raise ValueError("primary_task_id is not present in task plan")

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

    def to_dict(self) -> Dict[str, Any]:
        """生成可诊断且不重复建立权威的计划投影。"""
        return {
            "primary_task_id": self.primary_task_id,
            "reason": self.reason,
            "confidence": self.confidence,
            "tasks": [task.to_dict() for task in self.ordered_tasks],
        }


@dataclass(frozen=True)
class CoverageReport:
    """TaskPlan 与实际 outcomes 之间的完整性投影。"""

    complete: bool
    required_task_ids: Tuple[str, ...]
    completed_task_ids: Tuple[str, ...]
    failed_task_ids: Tuple[str, ...]
    missing_task_ids: Tuple[str, ...]
    unresolved_required_task_ids: Tuple[str, ...]
    duplicate_task_ids: Tuple[str, ...] = field(default_factory=tuple)
    unexpected_task_ids: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        """把不可变内部元组投影为 JSON 数组。"""
        return {
            "complete": self.complete,
            "required_task_ids": list(self.required_task_ids),
            "completed_task_ids": list(self.completed_task_ids),
            "failed_task_ids": list(self.failed_task_ids),
            "missing_task_ids": list(self.missing_task_ids),
            "unresolved_required_task_ids": list(self.unresolved_required_task_ids),
            "duplicate_task_ids": list(self.duplicate_task_ids),
            "unexpected_task_ids": list(self.unexpected_task_ids),
        }


@dataclass(frozen=True)
class ExecutionBudget:
    """一次编排请求允许使用的时间和并发 Agent 上限。"""

    request_timeout_s: float = 20.0
    agent_timeout_s: float = 15.0
    max_agents: int = 3

    def __post_init__(self) -> None:
        """配置错误应在启动时失败，不能退化成随机运行时行为。"""
        if self.request_timeout_s <= 0:
            raise ValueError("request_timeout_s must be positive")
        if self.agent_timeout_s <= 0:
            raise ValueError("agent_timeout_s must be positive")
        if self.max_agents < 1:
            raise ValueError("max_agents must be at least one")

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
