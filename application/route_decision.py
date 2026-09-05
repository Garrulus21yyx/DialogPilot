"""Legacy route data used by remaining authority and cost evaluation.

Target routing is implemented in turn_planning and conversation_agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RouteContractError(ValueError):
    pass


class RouteMode(str, Enum):
    DIRECT = "direct"
    KNOWLEDGE_QA = "knowledge_qa"
    AGENT_TASK = "agent_task"
    MIXED = "mixed"
    MULTI_DOMAIN = "multi_domain"
    CLARIFY = "clarify"
    HANDOFF = "handoff"
    OUT_OF_SCOPE = "out_of_scope"


class RouteRisk(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RequiredAuthority(str, Enum):
    KNOWLEDGE = "knowledge"
    ORDER_STATE = "order_state"
    REFUND_STATE = "refund_state"
    ACCOUNT_STATE = "account_state"
    SECURITY = "security"
    DOMAIN_TOOL = "domain_tool"
    ACTION_APPROVAL = "action_approval"
    HUMAN = "human"


class ComponentStatus(str, Enum):
    INVOKED = "INVOKED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class ComponentInvocation:
    component: str
    status: ComponentStatus
    reason_code: str
    input_fingerprint: str
    policy_version: str


@dataclass(frozen=True)
class RouteDecision:
    mode: RouteMode
    intent: str
    confidence: float
    required_authorities: tuple[RequiredAuthority, ...]
    risk: RouteRisk
    reason_codes: tuple[str, ...]
    missing_inputs: tuple[str, ...]
    owner_ids: tuple[str, ...]
    component_invocations: tuple[ComponentInvocation, ...]
    policy_version: str
    input_fingerprint: str
    auxiliary_signals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise RouteContractError("route confidence must be in [0,1]")
        if len(set(self.required_authorities)) != len(self.required_authorities):
            raise RouteContractError("required authorities must be unique")
        if not self.reason_codes:
            raise RouteContractError("route reason codes are required")
        if self.mode not in {
            RouteMode.DIRECT, RouteMode.OUT_OF_SCOPE,
            RouteMode.CLARIFY, RouteMode.HANDOFF,
        } and not self.required_authorities:
            raise RouteContractError("executable route requires authority")
        components = [item.component for item in self.component_invocations]
        if components != ["intent_fusion", "domain_routing", "instance_selection"]:
            raise RouteContractError("router component trace is incomplete or reordered")

    @property
    def legacy_planning_disposition(self) -> str:
        if self.mode is RouteMode.CLARIFY:
            return "clarify"
        if self.mode is RouteMode.OUT_OF_SCOPE:
            return "out_of_scope"
        return "execute"
