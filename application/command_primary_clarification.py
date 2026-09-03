"""Publish the existing CLARIFY terminal algebra for command-primary plans."""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from agents.agent_orchestrator import OrchestratorResult, PlanningDisposition
from application.command_primary_chat import CommandPrimaryChatPlan
from application.route_decision import RouteMode
from application.route_outcomes import NeedsInputDraft
from core.intent_recognizer import IntentCategory


_PROMPT = "请补充你希望处理的具体对象或必要信息，我再继续。"


def clarification_execution_result(
    request_id: str,
    identity: object,
    chat_plan: CommandPrimaryChatPlan,
) -> OrchestratorResult:
    plan = chat_plan.plan
    contract = chat_plan.execution_contract
    if (
        plan is None
        or plan.route.mode is not RouteMode.CLARIFY
        or contract is None
        or not contract.missing_inputs
    ):
        raise ValueError("clarification requires a compiled missing-input plan")

    identity_seed = f"{plan.plan_id}:{request_id}"
    draft = NeedsInputDraft(
        workflow_run_id=str(getattr(identity, "workflow_run_id")),
        signal_id=_id("clarify-signal", identity_seed),
        kind="user_input",
        expires_at=(
            datetime.now(timezone.utc) + timedelta(minutes=30)
        ).isoformat(),
        interaction_publication_id=_id("clarify-publication", identity_seed),
        missing_inputs=contract.missing_inputs,
        prompt=_PROMPT,
    )
    return OrchestratorResult(
        request_id=request_id,
        response=draft.prompt,
        agent_type=None,
        intent=IntentCategory.REQUEST,
        agent_types=[],
        primary_agent=None,
        routing_reason=plan.route.reason_code,
        routing_confidence=0.0,
        routing_disposition=PlanningDisposition.CLARIFY,
        synthesis_status="policy",
        synthesis_reason="command-primary missing-input publication",
        task_plan={},
        coverage={
            "complete": False,
            "missing_inputs": list(draft.missing_inputs),
        },
        pending_signals=[asdict(draft)],
        routing_policy_trace={
            "decision_source": "command_primary",
            "plan_id": plan.plan_id,
        },
    )


def _id(kind: str, seed: str) -> str:
    return f"{kind}:v1:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()
