"""RouteDecision normalization and demand-driven RouterInvocationPolicy."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from agents.routing_policy import (
    DomainDecision,
    InstanceSelectionDecision,
)


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


class RequestShape(str, Enum):
    UNKNOWN = "unknown"
    GREETING = "greeting"
    THANKS = "thanks"
    KNOWLEDGE_FAQ = "knowledge_faq"
    BUSINESS_STATE = "business_state"
    MIXED_POLICY_STATE = "mixed_policy_state"
    MULTI_DOMAIN_TASK = "multi_domain_task"
    MISSING_INPUT = "missing_input"
    EXPLICIT_HANDOFF = "explicit_handoff"
    OUT_OF_SCOPE = "out_of_scope"
    SECURITY_RISK = "security_risk"
    UNSUPPORTED_OPERATION = "unsupported_operation"


class ContinuationMode(str, Enum):
    CONTINUE = "continue"
    EXPAND = "expand"
    SWITCH = "switch"
    AMBIGUOUS = "ambiguous"


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
class IntentRouteSignal:
    intent: str
    confidence: float
    request_shape: RequestShape
    required_authorities: tuple[RequiredAuthority, ...]
    risk: RouteRisk
    missing_inputs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    policy_version: str = "intent-route-signal-v1"
    input_fingerprint: str = ""


class IntentDecisionPort(Protocol):
    def __call__(self) -> IntentRouteSignal: ...


class DomainDecisionPort(Protocol):
    def __call__(self) -> DomainDecision: ...


class InstanceDecisionPort(Protocol):
    def __call__(self, owner: str) -> InstanceSelectionDecision: ...


class RouteDecisionPort(Protocol):
    def decide(self, invocation: "RouterInvocation") -> "RouteDecision": ...


@dataclass(frozen=True)
class RouterInvocation:
    input_fingerprint: str
    request_shape: RequestShape = RequestShape.UNKNOWN
    continuation_mode: ContinuationMode | None = None
    prior_route_mode: RouteMode | None = None
    prior_intent: str = ""
    prior_authorities: tuple[RequiredAuthority, ...] = ()
    prior_risk: RouteRisk = RouteRisk.LOW
    pending_signal_reply: bool = False
    new_requirement_ambiguous: bool = False
    intent_port: IntentDecisionPort | None = None
    domain_port: DomainDecisionPort | None = None
    instance_port: InstanceDecisionPort | None = None
    owner_pool_sizes: Mapping[str, int] | None = None
    emotion_signal: str | None = None

    def __post_init__(self) -> None:
        if not self.input_fingerprint.strip():
            raise RouteContractError("router input fingerprint is required")


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


class RouterInvocationPolicy:
    version = "router-invocation-policy-v1"

    def decide(self, invocation: RouterInvocation) -> RouteDecision:
        if invocation.pending_signal_reply:
            mode = invocation.prior_route_mode or RouteMode.AGENT_TASK
            return self._resolved(
                invocation, mode=mode, intent=invocation.prior_intent,
                confidence=1.0, authorities=invocation.prior_authorities,
                risk=invocation.prior_risk,
                reasons=("PENDING_SIGNAL_RESUME",),
                component_reason="RESUME_PINNED_CHILD",
            )
        if invocation.continuation_mode is ContinuationMode.CONTINUE:
            if invocation.prior_route_mode is None:
                raise RouteContractError("CONTINUE requires pinned prior route")
            return self._resolved(
                invocation, mode=invocation.prior_route_mode,
                intent=invocation.prior_intent, confidence=1.0,
                authorities=invocation.prior_authorities,
                risk=invocation.prior_risk, reasons=("CONTINUE_PINNED_FRAME",),
                component_reason="CONTINUE_REUSES_PINNED_DECISIONS",
            )

        hard = _shape_contract(invocation.request_shape)
        if hard is not None and hard[0] in {
            RouteMode.DIRECT, RouteMode.KNOWLEDGE_QA, RouteMode.CLARIFY,
            RouteMode.HANDOFF, RouteMode.OUT_OF_SCOPE,
        }:
            return self._resolved(
                invocation, mode=hard[0], intent=hard[1], confidence=1.0,
                authorities=hard[2], risk=hard[3], reasons=(hard[4],),
                missing=hard[5], component_reason="REQUEST_SHAPE_DETERMINISTIC",
            )

        invoke_intent = (
            invocation.continuation_mode in {
                ContinuationMode.SWITCH, ContinuationMode.AMBIGUOUS,
            }
            or invocation.request_shape is RequestShape.UNKNOWN
            or (
                invocation.continuation_mode is ContinuationMode.EXPAND
                and invocation.new_requirement_ambiguous
            )
        )
        if invoke_intent:
            if invocation.intent_port is None:
                return self._clarify_unavailable(invocation)
            signal = invocation.intent_port()
            intent_trace = ComponentInvocation(
                "intent_fusion", ComponentStatus.INVOKED,
                "SEMANTIC_AMBIGUITY_REQUIRES_INTENT",
                signal.input_fingerprint or invocation.input_fingerprint,
                signal.policy_version,
            )
        else:
            deterministic = _shape_contract(invocation.request_shape)
            signal = IntentRouteSignal(
                intent=(
                    invocation.prior_intent
                    or (deterministic[1] if deterministic else "")
                ), confidence=1.0,
                request_shape=invocation.request_shape,
                required_authorities=(
                    invocation.prior_authorities
                    or (deterministic[2] if deterministic else ())
                ),
                risk=(
                    invocation.prior_risk
                    if invocation.continuation_mode is ContinuationMode.EXPAND
                    else deterministic[3] if deterministic else invocation.prior_risk
                ),
                reason_codes=("EXPAND_DETERMINISTIC_DELTA",),
            )
            intent_trace = self._component(
                "intent_fusion", ComponentStatus.SKIPPED,
                "EXPAND_DELTA_DETERMINISTIC", invocation,
            )
        resolved = _shape_contract(signal.request_shape)
        if resolved is None:
            return self._clarify_unavailable(invocation, intent_trace=intent_trace)
        mode, default_intent, default_authorities, default_risk, shape_reason, missing = resolved
        intent = signal.intent or default_intent
        authorities = signal.required_authorities or default_authorities
        risk = signal.risk or default_risk
        reasons = tuple(dict.fromkeys((*signal.reason_codes, shape_reason)))
        if mode in {
            RouteMode.DIRECT, RouteMode.KNOWLEDGE_QA, RouteMode.CLARIFY,
            RouteMode.HANDOFF, RouteMode.OUT_OF_SCOPE,
        }:
            return RouteDecision(
                mode, intent, signal.confidence, authorities, risk, reasons,
                signal.missing_inputs or missing, (), (
                    intent_trace,
                    self._component("domain_routing", ComponentStatus.SKIPPED,
                                    "ROUTE_HAS_NO_WORKER", invocation),
                    self._component("instance_selection", ComponentStatus.SKIPPED,
                                    "ROUTE_HAS_NO_WORKER", invocation),
                ), self.version, invocation.input_fingerprint,
                _auxiliary_signals(invocation),
            )
        if invocation.domain_port is None:
            return self._handoff_domain_unavailable(
                invocation, intent_trace, intent, authorities, risk, reasons,
            )
        domain = invocation.domain_port()
        domain_trace = ComponentInvocation(
            "domain_routing", ComponentStatus.INVOKED,
            domain.hard_rule_reason or "WORKER_ROUTE_REQUIRES_OWNER",
            domain.input_fingerprint, domain.policy_version,
        )
        owners = tuple(owner.value for owner in (
            domain.selected_owners or domain.ordered_owners
        ))
        if not owners:
            return self._handoff_domain_unavailable(
                invocation, intent_trace, intent, authorities, risk, reasons,
            )
        pool_sizes = dict(invocation.owner_pool_sizes or {})
        multi_pool_owners = tuple(owner for owner in owners if pool_sizes.get(owner, 0) > 1)
        if not multi_pool_owners:
            instance_trace = self._component(
                "instance_selection", ComponentStatus.NOT_APPLICABLE,
                "ALL_SELECTED_OWNER_POOLS_SINGLETON", invocation,
            )
        else:
            if invocation.instance_port is None:
                return self._handoff_domain_unavailable(
                    invocation, intent_trace, intent, authorities, risk, reasons,
                )
            decisions = tuple(
                invocation.instance_port(owner) for owner in multi_pool_owners
            )
            instance_trace = ComponentInvocation(
                "instance_selection", ComponentStatus.INVOKED,
                "MULTI_INSTANCE_OWNER_POOL",
                _hash([item.snapshot_fingerprint for item in decisions]),
                decisions[0].policy_version,
            )
        return RouteDecision(
            mode, intent, signal.confidence, authorities, risk, reasons,
            signal.missing_inputs or missing, owners,
            (intent_trace, domain_trace, instance_trace),
            self.version, invocation.input_fingerprint,
            _auxiliary_signals(invocation),
        )

    def _resolved(
        self, invocation, *, mode, intent, confidence, authorities, risk,
        reasons, component_reason, missing=(),
    ):
        return RouteDecision(
            mode, intent, confidence, authorities, risk, reasons, missing, (),
            tuple(self._component(
                component, ComponentStatus.SKIPPED, component_reason, invocation,
            ) for component in (
                "intent_fusion", "domain_routing", "instance_selection",
            )),
            self.version, invocation.input_fingerprint,
            _auxiliary_signals(invocation),
        )

    def _clarify_unavailable(self, invocation, *, intent_trace=None):
        intent_trace = intent_trace or self._component(
            "intent_fusion", ComponentStatus.NOT_APPLICABLE,
            "INTENT_PORT_UNAVAILABLE", invocation,
        )
        return RouteDecision(
            RouteMode.CLARIFY, "unknown", 0.0, (), RouteRisk.MEDIUM,
            ("ROUTER_INPUT_AMBIGUOUS",), ("request_goal",), (),
            (intent_trace,
             self._component("domain_routing", ComponentStatus.SKIPPED,
                             "INTENT_UNRESOLVED", invocation),
             self._component("instance_selection", ComponentStatus.SKIPPED,
                             "INTENT_UNRESOLVED", invocation)),
            self.version, invocation.input_fingerprint,
            _auxiliary_signals(invocation),
        )

    def _handoff_domain_unavailable(
        self, invocation, intent_trace, intent, authorities, risk, reasons,
    ):
        return RouteDecision(
            RouteMode.HANDOFF, intent, 0.0,
            tuple(dict.fromkeys((*authorities, RequiredAuthority.HUMAN))),
            max(risk, RouteRisk.HIGH, key=lambda item: list(RouteRisk).index(item)),
            tuple(dict.fromkeys((*reasons, "DOMAIN_OWNER_UNAVAILABLE"))), (), (),
            (intent_trace,
             self._component("domain_routing", ComponentStatus.NOT_APPLICABLE,
                             "DOMAIN_PORT_OR_OWNER_UNAVAILABLE", invocation),
             self._component("instance_selection", ComponentStatus.SKIPPED,
                             "DOMAIN_OWNER_UNAVAILABLE", invocation)),
            self.version, invocation.input_fingerprint,
            _auxiliary_signals(invocation),
        )

    @staticmethod
    def _component(component, status, reason, invocation):
        return ComponentInvocation(
            component, status, reason, invocation.input_fingerprint,
            RouterInvocationPolicy.version,
        )


def _shape_contract(shape: RequestShape):
    mapping = {
        RequestShape.GREETING: (
            RouteMode.DIRECT, "greeting", (), RouteRisk.LOW,
            "DETERMINISTIC_GREETING", (),
        ),
        RequestShape.THANKS: (
            RouteMode.DIRECT, "thanks", (), RouteRisk.LOW,
            "DETERMINISTIC_THANKS", (),
        ),
        RequestShape.KNOWLEDGE_FAQ: (
            RouteMode.KNOWLEDGE_QA, "knowledge_query",
            (RequiredAuthority.KNOWLEDGE,), RouteRisk.LOW,
            "STATIC_KNOWLEDGE_REQUEST", (),
        ),
        RequestShape.BUSINESS_STATE: (
            RouteMode.AGENT_TASK, "business_state",
            (RequiredAuthority.DOMAIN_TOOL,), RouteRisk.MEDIUM,
            "PERSONAL_STATE_REQUIRES_TOOL", (),
        ),
        RequestShape.MIXED_POLICY_STATE: (
            RouteMode.MIXED, "mixed_policy_state",
            (RequiredAuthority.KNOWLEDGE, RequiredAuthority.DOMAIN_TOOL),
            RouteRisk.MEDIUM, "MIXED_AUTHORITY_REQUEST", (),
        ),
        RequestShape.MULTI_DOMAIN_TASK: (
            RouteMode.MULTI_DOMAIN, "multi_domain",
            (RequiredAuthority.DOMAIN_TOOL,), RouteRisk.MEDIUM,
            "MULTI_DOMAIN_REQUEST", (),
        ),
        RequestShape.MISSING_INPUT: (
            RouteMode.CLARIFY, "missing_input", (), RouteRisk.LOW,
            "MISSING_REQUIRED_INPUT", ("request_goal",),
        ),
        RequestShape.EXPLICIT_HANDOFF: (
            RouteMode.HANDOFF, "human_handoff", (RequiredAuthority.HUMAN,),
            RouteRisk.HIGH, "EXPLICIT_HUMAN_REQUEST", (),
        ),
        RequestShape.OUT_OF_SCOPE: (
            RouteMode.OUT_OF_SCOPE, "out_of_scope", (), RouteRisk.LOW,
            "EXPLICIT_OUT_OF_SCOPE", (),
        ),
        RequestShape.SECURITY_RISK: (
            RouteMode.AGENT_TASK, "account_security",
            (RequiredAuthority.SECURITY, RequiredAuthority.DOMAIN_TOOL),
            RouteRisk.CRITICAL, "SECURITY_HARD_RULE", (),
        ),
        RequestShape.UNSUPPORTED_OPERATION: (
            RouteMode.HANDOFF, "unsupported_operation",
            (RequiredAuthority.HUMAN,), RouteRisk.HIGH,
            "UNSUPPORTED_OPERATION_REQUIRES_HANDOFF", (),
        ),
    }
    return mapping.get(shape)


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _auxiliary_signals(invocation: RouterInvocation) -> tuple[str, ...]:
    signal = str(invocation.emotion_signal or "").strip()
    return (f"emotion:{signal}",) if signal else ()
