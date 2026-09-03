"""Publish a command-primary OUT_OF_SCOPE policy terminal."""
from __future__ import annotations

from agents.agent_orchestrator import OrchestratorResult, PlanningDisposition
from application.command_primary_chat import CommandPrimaryChatPlan
from application.route_decision import RouteMode
from core.intent_recognizer import IntentCategory


_RESPONSE = (
    "我是 DialogPilot 客服助手，主要处理订单物流、退款账单、账户安全和产品技术问题。"
    "这个问题超出了客服范围；如果您有上述相关诉求，我可以继续协助。"
)


def out_of_scope_execution_result(
    request_id: str,
    chat_plan: CommandPrimaryChatPlan,
) -> OrchestratorResult:
    plan = chat_plan.plan
    contract = chat_plan.execution_contract
    if (
        plan is None
        or plan.route.mode is not RouteMode.OUT_OF_SCOPE
        or contract is None
    ):
        raise ValueError("out-of-scope requires a compiled policy-terminal plan")

    return OrchestratorResult(
        request_id=request_id,
        response=_RESPONSE,
        agent_type=None,
        intent=IntentCategory.OTHER,
        agent_types=[],
        primary_agent=None,
        routing_reason=plan.route.reason_code,
        routing_confidence=0.0,
        routing_disposition=PlanningDisposition.OUT_OF_SCOPE,
        synthesis_status="policy",
        synthesis_reason="command-primary business-scope terminal",
        task_plan={},
        coverage={"complete": True},
        routing_policy_trace={
            "decision_source": "command_primary",
            "plan_id": plan.plan_id,
        },
    )
