"""Small bridge from state-first planning to the existing chat lifecycle."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from agents.orchestration_contracts import AgentType, TaskEffect
from application.active_case import ActiveCaseState
from application.authority_policy import AuthorityPolicyRegistry, FactRequirement
from application.command_primary_planner import (
    CommandPrimaryPlanner,
    PlanningResult,
    PlanningStatus,
)
from application.coverage_gate import VerificationProfileRegistry
from application.flow_state import FlowStateAggregate, FlowStateError, FlowStateStore
from application.route_decision import RouteMode
from application.route_execution import RouteExecutionContract, RouteExecutionPolicy
from application.route_policy_v2 import FlowActionRegistry
from application.turn_plan import FlowMutationKind, TurnPlan
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
    flow_state: FlowStateAggregate | None = None
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
        primary_route_modes: tuple[RouteMode, ...] = (),
        flow_state_store: FlowStateStore | None = None,
    ) -> None:
        self._planner = planner
        self._registry_factory = registry_factory
        self._primary_route_modes = frozenset(primary_route_modes)
        self._flow_state_store = flow_state_store
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
        principal = PrincipalScope(
            str(identity.tenant_id),
            str(identity.user_id),
            str(identity.conversation_id),
        )
        flow_state = (
            await asyncio.to_thread(self._flow_state_store.load, principal)
            if self._flow_state_store is not None else None
        )
        state = _turn_state(
            identity,
            recent_messages,
            active_case_view,
            principal=principal,
            flow_state=flow_state,
        )
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
            return CommandPrimaryChatPlan(
                planning=planning,
                plan=None,
                flow_state=flow_state,
            )
        if plan.route.mode not in {
            RouteMode.KNOWLEDGE_QA,
            RouteMode.AGENT_TASK,
            RouteMode.CLARIFY,
        }:
            return CommandPrimaryChatPlan(
                planning=planning,
                plan=plan,
                flow_state=flow_state,
            )
        if (
            plan.route.mode is RouteMode.AGENT_TASK
            and any(
                task.effect is not TaskEffect.READ_ONLY
                for task in plan.work.graph.tasks
            )
        ):
            return CommandPrimaryChatPlan(
                planning=planning,
                plan=plan,
                flow_state=flow_state,
            )

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
            work=plan.work,
        )
        primary_owner = (
            plan.work.graph.tasks[0].owner
            if plan.work is not None else AgentType.GENERAL
        )
        projected_intent = (
            IntentCategory.QUERY
            if plan.route.mode is RouteMode.KNOWLEDGE_QA
            else {
                AgentType.BILLING: IntentCategory.REFUND,
                AgentType.TECHNICAL: IntentCategory.TECHNICAL,
                AgentType.ACCOUNT_SECURITY: IntentCategory.ACCOUNT_SECURITY,
            }.get(primary_owner, IntentCategory.REQUEST)
        )
        projected_group = (
            "query"
            if plan.route.mode is RouteMode.KNOWLEDGE_QA
            else primary_owner.value
        )
        projection = IntentResult(
            intent=projected_intent,
            confidence=0.0,
            urgency=UrgencyLevel.LOW,
            intent_group=projected_group,
            entities={},
            reasoning="post-decision compatibility projection",
            latency_ms=0.0,
            source_scores={"command_primary_projection": 1.0},
            classifier_fingerprint="",
            input_fingerprint=plan.plan_id,
        )
        return CommandPrimaryChatPlan(
            planning=planning,
            plan=plan,
            flow_state=flow_state,
            requirements=requirements,
            execution_contract=execution,
            intent_projection=projection,
            use_primary=plan.route.mode in self._primary_route_modes,
        )

    async def commit_flow_transition(
        self,
        chat_plan: CommandPrimaryChatPlan,
    ) -> bool:
        plan = chat_plan.plan
        transitions = plan.transitions if plan else None
        if transitions is None:
            return True
        if self._flow_state_store is None or chat_plan.flow_state is None:
            raise FlowStateError("flow transition has no state owner")
        if len(transitions.mutations) != 1:
            raise FlowStateError("read-only primary supports one flow transition")
        mutation = transitions.mutations[0]
        if mutation.kind is FlowMutationKind.ADVANCE:
            next_state = chat_plan.flow_state.advance_flow(
                mutation.source_instance_id,
                expected_version=mutation.expected_source_version,
            )
        elif mutation.kind is FlowMutationKind.START:
            next_state = chat_plan.flow_state.start_flow(
                mutation.target_flow,
                command_id=mutation.command_id,
            )
        else:
            raise FlowStateError("read-only primary transition is unsupported")
        return await asyncio.to_thread(
            self._flow_state_store.compare_and_set,
            chat_plan.flow_state,
            next_state,
        )


def _turn_state(
    identity: Any,
    recent_messages: Sequence[Any],
    active_case_view: Any,
    *,
    principal: PrincipalScope,
    flow_state: FlowStateAggregate | None,
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
        principal=principal,
        flow_aggregate=flow_state.aggregate if flow_state else None,
        active_flows=flow_state.active_flows if flow_state else (),
        pending_slot=flow_state.pending_slot if flow_state else None,
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
            *((StateSourceStatus(
                "flow_state",
                StateAvailability.CURRENT,
                (
                    f"{flow_state.schema_version}:"
                    f"{flow_state.aggregate.version}"
                ),
            ),) if flow_state else ()),
        ),
        captured_at=datetime.now(timezone.utc),
        producer_version="command-primary-chat-state-v1",
    )
