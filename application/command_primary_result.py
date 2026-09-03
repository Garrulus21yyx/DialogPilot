"""Project native command-primary results onto the current response shell."""
from __future__ import annotations

from typing import Any

from agents.agent_orchestrator import OrchestratorResult, PlanningDisposition
from application.command_primary_chat import CommandPrimaryChatPlan
from core.intent_recognizer import IntentCategory


def knowledge_execution_result(
    request_id: str,
    chat_plan: CommandPrimaryChatPlan,
    knowledge: Any,
) -> OrchestratorResult:
    plan = chat_plan.plan
    if plan is None or plan.work is None:
        raise ValueError("knowledge execution requires a compiled work plan")
    response = str(getattr(knowledge, "answer", "") or "").strip()
    if not response:
        response = "当前知识库没有足够证据回答这个问题。"
    complete = bool(getattr(knowledge, "used", False)) and bool(
        getattr(knowledge, "answer", "") or getattr(knowledge, "abstained", False)
    )
    return OrchestratorResult(
        request_id=request_id,
        response=response,
        agent_type=None,
        intent=IntentCategory.QUERY,
        agent_types=[],
        primary_agent=None,
        routing_reason=plan.route.reason_code,
        routing_confidence=0.0,
        routing_disposition=PlanningDisposition.EXECUTE,
        synthesis_status="grounded_knowledge",
        synthesis_reason="command-primary Knowledge candidate",
        task_plan=plan.work.graph.to_dict(),
        coverage={"complete": complete},
        execution_budget={},
        bundle_version="",
        routing_policy_trace={
            "decision_source": "command_primary",
            "plan_id": plan.plan_id,
        },
    )


def read_only_execution_result(
    request_id: str,
    chat_plan: CommandPrimaryChatPlan,
    execution: Any,
) -> OrchestratorResult:
    plan = chat_plan.plan
    if plan is None or plan.work is None:
        raise ValueError("read-only execution requires a compiled work plan")
    return OrchestratorResult(
        request_id=request_id,
        response=execution.response,
        agent_type=execution.owner,
        intent=IntentCategory.REFUND,
        agent_types=[execution.owner],
        primary_agent=execution.owner,
        routing_reason=plan.route.reason_code,
        routing_confidence=0.0,
        routing_disposition=PlanningDisposition.EXECUTE,
        synthesis_status="authoritative_read",
        synthesis_reason="command-primary read-only work",
        agent_outcomes=[{
            "task_id": execution.task_id,
            "agent_type": execution.owner.value,
            "status": "success" if execution.tool_result.success else "error",
            "is_primary": True,
        }],
        task_plan=plan.work.graph.to_dict(),
        coverage=execution.coverage,
        execution_budget={},
        bundle_version="",
        routing_policy_trace={
            "decision_source": "command_primary",
            "plan_id": plan.plan_id,
        },
    )


def media_read_execution_result(
    request_id: str,
    chat_plan: CommandPrimaryChatPlan,
    execution: Any,
) -> OrchestratorResult:
    plan = chat_plan.plan
    if plan is None or plan.work is None:
        raise ValueError("media read requires a compiled work plan")
    return OrchestratorResult(
        request_id=request_id,
        response=execution.response,
        agent_type=execution.owner,
        intent=IntentCategory.TECHNICAL,
        agent_types=[execution.owner],
        primary_agent=execution.owner,
        routing_reason=plan.route.reason_code,
        routing_confidence=0.0,
        routing_disposition=PlanningDisposition.EXECUTE,
        synthesis_status="media_evidence_read",
        synthesis_reason="command-primary L1 media work",
        agent_outcomes=[{
            "task_id": execution.task_id,
            "agent_type": execution.owner.value,
            "status": "success",
            "is_primary": True,
        }],
        task_plan=plan.work.graph.to_dict(),
        coverage=execution.coverage,
        execution_budget={},
        bundle_version="",
        routing_policy_trace={
            "decision_source": "command_primary",
            "plan_id": plan.plan_id,
        },
    )
