"""One small vertical slice through state-first command-primary planning."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from application.route_policy_v2 import (
    FlowActionRegistry,
    RoutePolicy,
    RoutePolicyStatus,
)
from application.turn_plan import TurnPlan, TurnPlanCompiler
from application.turn_state import TurnStateSnapshot
from application.turn_understanding import (
    PendingSlotResolver,
    UnderstandingResult,
    UnderstandingStatus,
    fingerprint_message,
)


class SemanticUnderstandingPort(Protocol):
    def understand(
        self,
        message: str,
        message_fingerprint: str,
        state: TurnStateSnapshot,
    ) -> UnderstandingResult: ...


class PlanningStatus(str, Enum):
    PLANNED = "PLANNED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class PlanningResult:
    status: PlanningStatus
    understanding: UnderstandingResult
    plan: TurnPlan | None
    semantic_router_used: bool
    reason_code: str


class CommandPrimaryPlanner:
    def __init__(
        self,
        deterministic: PendingSlotResolver,
        semantic: SemanticUnderstandingPort,
        route_policy: RoutePolicy,
        compiler: TurnPlanCompiler,
    ) -> None:
        self._deterministic = deterministic
        self._semantic = semantic
        self._route_policy = route_policy
        self._compiler = compiler

    def plan(
        self,
        message: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> PlanningResult:
        message_fingerprint = fingerprint_message(message)
        understanding = self._deterministic.resolve(
            message,
            message_fingerprint,
            state,
        )
        semantic_used = understanding.status is UnderstandingStatus.DEFER
        if semantic_used:
            understanding = self._semantic.understand(
                message,
                message_fingerprint,
                state,
            )
        accepted = self._route_policy.accept(understanding, state, registry)
        if accepted.status is RoutePolicyStatus.FAILURE:
            return PlanningResult(
                PlanningStatus.FAILED,
                understanding,
                None,
                semantic_used,
                accepted.reason_code,
            )
        plan = self._compiler.compile(accepted, state, registry)
        return PlanningResult(
            PlanningStatus.PLANNED,
            understanding,
            plan,
            semantic_used,
            accepted.reason_code,
        )
