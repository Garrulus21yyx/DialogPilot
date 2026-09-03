"""The small production registry enabled during command-primary migration."""
from __future__ import annotations

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.route_policy_v2 import (
    ActionDefinition,
    ApprovalPolicy,
    FlowActionRegistry,
    FlowDefinition,
    WorkKind,
)
from application.turn_state import FlowDefinitionRef
from application.turn_understanding import CommandKind


REFUND_STATUS = FlowDefinitionRef("refund_status", "v1")


def command_primary_flow_registry(tenant_id: str) -> FlowActionRegistry:
    """Register only the read-only paths enabled by the migration."""

    return FlowActionRegistry(
        tenant_id=tenant_id,
        generation="command-primary-read-only-v1",
        flows=(FlowDefinition(
            REFUND_STATUS,
            (CommandKind.CONTINUE_FLOW,),
        ),),
        actions=(
            ActionDefinition(
                action_id="knowledge.answer",
                version="v1",
                command_kind=CommandKind.ANSWER_KNOWLEDGE,
                flow=None,
                work_kind=WorkKind.KNOWLEDGE,
                owner=AgentType.GENERAL,
                effect=TaskEffect.READ_ONLY,
                risk=TaskRisk.LOW,
                requirement_ids=("knowledge.active_source",),
                allowed_tools=("knowledge_search",),
                approval=ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                objective="answer from grounded product knowledge",
            ),
            ActionDefinition(
                action_id="refund.status.read",
                version="v1",
                command_kind=CommandKind.CONTINUE_FLOW,
                flow=REFUND_STATUS,
                work_kind=WorkKind.AGENT,
                owner=AgentType.BILLING,
                effect=TaskEffect.READ_ONLY,
                risk=TaskRisk.LOW,
                requirement_ids=("refund.current_state",),
                allowed_tools=("refund_status",),
                approval=ApprovalPolicy.USER_COMMAND_SUFFICIENT,
                objective="read the current refund status",
            ),
        ),
    )
