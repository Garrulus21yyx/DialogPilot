"""The small production registry enabled during command-primary migration."""
from __future__ import annotations

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.route_policy_v2 import (
    ActionDefinition,
    ApprovalPolicy,
    FlowActionRegistry,
    WorkKind,
)
from application.turn_understanding import CommandKind


def knowledge_flow_registry(tenant_id: str) -> FlowActionRegistry:
    """Register the first primary path without defining unrelated flows."""

    return FlowActionRegistry(
        tenant_id=tenant_id,
        generation="command-primary-knowledge-v1",
        flows=(),
        actions=(ActionDefinition(
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
        ),),
    )
