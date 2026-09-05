"""Legacy outcome coverage used only by remaining stateful evaluation fixtures."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Sequence

from agents.orchestration_contracts import CoverageReport, PendingSignal, TaskArtifact, TaskPlan
from mcp.tool_manager import ToolExecutionReceipt


class AgentOutcomeStatus(str, Enum):
    """单个 Agent 一次执行能够产生的闭合状态。"""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    ERROR = "error"
    BUDGET_EXCEEDED = "budget_exceeded"
    BLOCKED_DEPENDENCY = "blocked_dependency"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    # Read compatibility only. Runtime execution must represent waiting through
    # PendingSignal, never by adding this value to task_outcomes.
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass(frozen=True)
class AgentOutcome:
    """单 Agent 执行证据；内容与错误不会通过异常通道隐式丢失。"""
    task_id: str
    required: bool
    agent_type: str
    status: AgentOutcomeStatus
    is_primary: bool
    agent_key: str = ""
    responding_agent_type: str = ""
    content: str = ""
    confidence: float = 0.0
    latency_ms: float = 0.0
    escalate: bool = False
    error: str = ""
    react_status: str = "disabled"
    react_steps: int = 0
    tool_call_ids: List[str] = field(default_factory=list)
    react_run_id: str = ""
    pending_approval_call_ids: List[str] = field(default_factory=list)
    artifacts: tuple[TaskArtifact, ...] = ()
    authority_conflicts: List[str] = field(default_factory=list)
    tool_receipts: tuple[ToolExecutionReceipt, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        """转换为 API 可序列化字典，并显式展开枚举值。"""
        data = asdict(self)
        data["status"] = self.status.value
        return data


class CoverageGate:
    """比较计划和 outcomes，拥有“必需任务是否全部完成”的判定权。"""

    @staticmethod
    def evaluate(plan: TaskPlan, outcomes: Sequence[AgentOutcome]) -> CoverageReport:
        """以 task_id 对齐终态事实；PendingSignal 仅投影等待，不算 outcome。"""
        return CoverageGate.evaluate_with_signals(plan, outcomes, ())

    @staticmethod
    def evaluate_with_signals(
        plan: TaskPlan,
        outcomes: Sequence[AgentOutcome],
        pending_signals: Sequence[PendingSignal],
    ) -> CoverageReport:
        """Project terminal coverage and native interrupts without conflating them."""
        plan_ids = [task.task_id for task in plan.ordered_tasks]
        required_ids = [task.task_id for task in plan.ordered_tasks if task.required]
        outcome_ids = [outcome.task_id for outcome in outcomes]
        signal_ids = [signal.task_id for signal in pending_signals]
        seen: set[str] = set()
        duplicate_ids: list[str] = []
        for task_id in (*outcome_ids, *signal_ids):
            if task_id in seen and task_id not in duplicate_ids:
                duplicate_ids.append(task_id)
            seen.add(task_id)

        outcome_by_id = {
            outcome.task_id: outcome
            for outcome in outcomes
            if outcome.task_id in plan_ids
        }
        completed_ids = [
            task_id for task_id in plan_ids
            if task_id in outcome_by_id
            and outcome_by_id[task_id].status is AgentOutcomeStatus.SUCCESS
        ]
        failed_ids = [
            task_id for task_id in plan_ids
            if task_id in outcome_by_id
            and outcome_by_id[task_id].status is not AgentOutcomeStatus.SUCCESS
        ]
        pending_plan_ids = [
            task_id for task_id in plan_ids if task_id in set(signal_ids)
        ]
        missing_ids = [
            task_id for task_id in plan_ids
            if task_id not in outcome_by_id and task_id not in set(signal_ids)
        ]
        unresolved_required = [
            task_id for task_id in required_ids if task_id not in completed_ids
        ]
        unexpected_ids = list(dict.fromkeys(
            task_id for task_id in (*outcome_ids, *signal_ids) if task_id not in plan_ids
        ))
        complete = (
            not unresolved_required
            and not duplicate_ids
            and not unexpected_ids
            and not pending_plan_ids
        )
        return CoverageReport(
            complete=complete,
            required_task_ids=tuple(required_ids),
            completed_task_ids=tuple(completed_ids),
            failed_task_ids=tuple(failed_ids),
            missing_task_ids=tuple(missing_ids),
            unresolved_required_task_ids=tuple(unresolved_required),
            duplicate_task_ids=tuple(duplicate_ids),
            unexpected_task_ids=tuple(unexpected_ids),
            awaiting_signal_task_ids=tuple(pending_plan_ids),
            awaiting_signal_kinds=tuple(dict.fromkeys(
                signal.kind.value
                for signal in pending_signals
                if signal.task_id in plan_ids
            )),
        )
