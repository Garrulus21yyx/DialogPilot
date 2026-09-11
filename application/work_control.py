"""Trusted cooperative-steering checks shared by Target execution boundaries."""
from __future__ import annotations

from application.agent_result import AgentResult, AgentResultStatus
from application.conversation_state import ConversationStateStore
from application.work_item import WorkItem, prerequisite_ids
from core.identity import ConversationId, TenantId, UserId


class WorkSuperseded(RuntimeError):
    """The accepted revision changed while this work was running."""


def current_result_board(plan, board, state):
    """Retired reads are history; committed writes keep their receipts."""
    if board is None or plan.work is None:
        return board
    from application.capability_registry import CapabilityEffect
    from application.result_board import ResultBoard
    by_id = {item.work_item_id: item for item in plan.work.items}
    provenance = (*plan.work.items, *(item for item, _ in board.retained_outcomes))

    def current_result(item, outcome):
        if (outcome is None or item.effect is not CapabilityEffect.READ
                or outcome.status not in {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL,
                    AgentResultStatus.NEEDS_USER_INPUT, AgentResultStatus.WAITING_APPROVAL,
                    AgentResultStatus.NEEDS_EVIDENCE}):
            return outcome
        if not state.accepts_work(item) or not state.accepts_work(
                item, dependency_work_ids=prerequisite_ids(item, provenance)):
            return AgentResult(item.work_item_id, item.owner_agent,
                AgentResultStatus.SUPERSEDED, "WORK_CONTROL_SUPERSEDED", WorkControlGuard.version)
        return outcome

    results = tuple(current_result(by_id[r.work_item_id], r) for r in board.results)
    retained = tuple((item, current_result(item, outcome)) for item, outcome in board.retained_outcomes)
    return ResultBoard().evaluate(plan.work, results, retained_outcomes=retained)


class WorkControlGuard:
    """Read the authoritative conversation aggregate at a safe execution boundary."""

    version = "work-control-guard-v1"

    def __init__(self, state_store: ConversationStateStore) -> None:
        self._state_store = state_store

    def is_current(self, item: WorkItem, trusted_context) -> bool:
        if item.control is None:
            raise ValueError("Target work item lacks a control binding")
        state = self._state_store.load(
            TenantId(str(trusted_context["tenant_id"])),
            UserId(str(trusted_context["user_id"])),
            ConversationId(str(trusted_context["conversation_id"])),
        )
        if item.dependencies and "dependency_work_ids" not in trusted_context:
            raise ValueError("dependent work lacks its compiled prerequisite closure")
        return state.accepts_work(item, dependency_work_ids=trusted_context.get("dependency_work_ids", ()))

    def ensure_current(self, item: WorkItem, trusted_context) -> None:
        if not self.is_current(item, trusted_context):
            raise WorkSuperseded(
                f"work control is stale: {item.control.control_id}:v{item.control.revision}"
            )

    @staticmethod
    def superseded_result(item: WorkItem) -> AgentResult:
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUPERSEDED,
            "WORK_CONTROL_SUPERSEDED",
            WorkControlGuard.version,
        )
