"""Trusted cooperative-steering checks shared by Target execution boundaries."""
from __future__ import annotations

from application.agent_result import AgentResult, AgentResultStatus
from application.conversation_state import ConversationStateStore
from application.work_item import WorkItem
from core.identity import ConversationId, TenantId, UserId


class WorkSuperseded(RuntimeError):
    """The accepted revision changed while this work was running."""


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
        return state.accepts(item.control)

    def ensure_current(self, item: WorkItem, trusted_context) -> None:
        if not self.is_current(item, trusted_context):
            raise WorkSuperseded(
                f"work control is stale: {item.control.control_id}:v{item.control.revision}"
            )

    def superseded_result(self, item: WorkItem) -> AgentResult:
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUPERSEDED,
            "WORK_CONTROL_SUPERSEDED",
            self.version,
        )
