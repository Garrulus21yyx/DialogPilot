"""Small bridge from state-first planning to the existing chat lifecycle."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from application.active_case import ActiveCaseState
from application.authority_policy import AuthorityPolicyRegistry, FactRequirement
from application.command_primary_planner import (
    CommandPrimaryPlanner,
    PlanningResult,
    PlanningStatus,
)
from application.coverage_gate import VerificationProfileRegistry
from application.route_decision import RouteMode
from application.route_execution import RouteExecutionContract, RouteExecutionPolicy
from application.route_policy_v2 import FlowActionRegistry
from application.turn_plan import TurnPlan
from application.turn_state import (
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateSnapshot,
)
from core.intent_recognizer import IntentCategory, IntentResult, UrgencyLevel


@dataclass(frozen=True)
class CommandPrimaryChatPlan:
    planning: PlanningResult
    plan: TurnPlan | None
    requirements: tuple[FactRequirement, ...] = ()
    execution_contract: RouteExecutionContract | None = None
    intent_projection: IntentResult | None = None
    use_primary: bool = False


class CommandPrimaryChatPlanner:
    """Prepare one selective primary route; all other routes remain legacy."""

    def __init__(
        self,
        planner: CommandPrimaryPlanner,
        registry_factory: Callable[[str], FlowActionRegistry],
        *,
        knowledge_primary: bool = False,
    ) -> None:
        self._planner = planner
        self._registry_factory = registry_factory
        self._knowledge_primary = knowledge_primary
        self._authority = AuthorityPolicyRegistry.v1()
        self._verification = VerificationProfileRegistry()
        self._execution = RouteExecutionPolicy()

    async def prepare(
        self,
        *,
        message: str,
        identity: Any,
        recent_messages: Sequence[Any],
        active_case_view: Any,
        history: tuple[Mapping[str, str], ...],
        bundle: Any,
    ) -> CommandPrimaryChatPlan:
        state = _turn_state(identity, recent_messages, active_case_view)
        registry = self._registry_factory(state.principal.tenant_id)
        planning = await self._planner.plan(
            message,
            state,
            registry,
            history=history,
            bundle=bundle,
        )
        plan = planning.plan
        if planning.status is not PlanningStatus.PLANNED or plan is None:
            return CommandPrimaryChatPlan(planning, None)
        if plan.route.mode is not RouteMode.KNOWLEDGE_QA:
            return CommandPrimaryChatPlan(planning, plan)

        requirements = self._authority.requirements_for_ids(
            plan.route.requirement_ids,
        )
        verification = self._verification.contract_for(
            plan.route.mode,
            requirements,
        )
        execution = self._execution.plan_command_primary(
            plan.route,
            verification,
            input_fingerprint=plan.plan_id,
        )
        projection = IntentResult(
            intent=IntentCategory.QUERY,
            confidence=0.0,
            urgency=UrgencyLevel.LOW,
            intent_group="query",
            entities={},
            reasoning="post-decision compatibility projection",
            latency_ms=0.0,
            source_scores={"command_primary_projection": 1.0},
            classifier_fingerprint="",
            input_fingerprint=plan.plan_id,
        )
        return CommandPrimaryChatPlan(
            planning,
            plan,
            requirements,
            execution,
            projection,
            self._knowledge_primary,
        )


def _turn_state(
    identity: Any,
    recent_messages: Sequence[Any],
    active_case_view: Any,
) -> TurnStateSnapshot:
    projection = getattr(active_case_view, "projection", None)
    case_state = getattr(projection, "state", ActiveCaseState.NO_ACTIVE_CASE)
    case_availability = (
        StateAvailability.UNAVAILABLE
        if case_state is ActiveCaseState.UNAVAILABLE
        else StateAvailability.CURRENT
    )
    case_reasons = tuple(getattr(projection, "reason_codes", ()) or ())
    return TurnStateSnapshot(
        request_id=str(identity.request_id),
        principal=PrincipalScope(
            str(identity.tenant_id),
            str(identity.user_id),
            str(identity.conversation_id),
        ),
        flow_aggregate=None,
        active_flows=(),
        pending_signal=None,
        recent_turn_refs=tuple(
            str(item.message_id)
            for item in recent_messages
            if str(getattr(item, "message_id", "")).strip()
        ),
        active_case_refs=tuple(
            getattr(getattr(active_case_view, "selection", None), "case_ids", ())
        ),
        reusable_media_refs=(),
        sources=(
            StateSourceStatus(
                "working_memory",
                StateAvailability.CURRENT,
                "current-thread-memory-v1",
            ),
            StateSourceStatus(
                "active_case",
                case_availability,
                str(getattr(projection, "producer", "active-case-v1")),
                case_reasons[0] if case_reasons else (
                    "ACTIVE_CASE_UNAVAILABLE"
                    if case_availability is StateAvailability.UNAVAILABLE else ""
                ),
            ),
        ),
        captured_at=datetime.now(timezone.utc),
        producer_version="command-primary-chat-state-v1",
    )


def knowledge_execution_result(
    request_id: str,
    chat_plan: CommandPrimaryChatPlan,
    knowledge: Any,
) -> Any:
    """Project an executed Knowledge work item onto the legacy response shell."""

    from agents.agent_orchestrator import OrchestratorResult, PlanningDisposition

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
