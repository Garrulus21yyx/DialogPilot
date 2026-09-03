"""Versioned execution and publication contract for the eight RouteMode paths."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json

from application.coverage_gate import (
    SemanticVerifierPolicy,
    VerificationProfileContract,
)
from application.route_decision import (
    RequiredAuthority,
    RouteDecision,
    RouteMode,
)
from application.turn_plan import RouteDecisionV2


class RouteExecutionContractError(ValueError):
    pass


class RouteComponent(str, Enum):
    RULE_RESPONSE = "rule_response"
    MISSING_INPUT_SIGNAL = "missing_input_signal"
    PRE_ROUTE_RETRIEVER = "pre_route_retriever"
    GROUNDED_ANSWER_GENERATOR = "grounded_answer_generator"
    AGENT_ORCHESTRATOR = "agent_orchestrator"
    AGENT_KNOWLEDGE_TOOL = "agent_knowledge_tool"
    BUSINESS_TOOL = "business_tool"
    TASK_GRAPH = "task_graph"
    REQUIREMENT_COVERAGE_GATE = "requirement_coverage_gate"
    CITATION_CLAIM_GATE = "citation_claim_gate"
    CONDITIONAL_SYNTHESIS = "conditional_synthesis"
    SEMANTIC_VERIFIER = "semantic_verifier"
    HANDOFF_DRAFT = "handoff_draft"
    HANDOFF_WRITE = "handoff_write"
    TURN_RECORD = "turn_record"


class CandidateOwner(str, Enum):
    RULE_POLICY = "rule_policy"
    GROUNDED_ANSWER_GENERATOR = "grounded_answer_generator"
    AGENT = "agent"
    MIXED_AUTHORITY_AGENT = "mixed_authority_agent"
    TASK_GRAPH = "task_graph"
    HANDOFF_DRAFT = "handoff_draft"


class RouteExpectedOutcome(str, Enum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    HANDOFF_DRAFT = "handoff_draft"


@dataclass(frozen=True)
class RouteExecutionContract:
    mode: RouteMode
    candidate_owner: CandidateOwner
    expected_outcome: RouteExpectedOutcome
    required_components: tuple[RouteComponent, ...]
    conditional_components: tuple[RouteComponent, ...]
    forbidden_components: tuple[RouteComponent, ...]
    verification_profile: str
    deterministic_gates: tuple[str, ...]
    policy_version: str
    route_policy_version: str
    input_fingerprint: str
    missing_inputs: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    risk: str = "low"

    def __post_init__(self) -> None:
        required = set(self.required_components)
        conditional = set(self.conditional_components)
        forbidden = set(self.forbidden_components)
        if required & conditional or required & forbidden or conditional & forbidden:
            raise RouteExecutionContractError("route component sets must be disjoint")
        if required | conditional | forbidden != set(RouteComponent):
            raise RouteExecutionContractError("route component algebra must be exhaustive")
        if RouteComponent.TURN_RECORD not in required:
            raise RouteExecutionContractError("every route must record the turn")
        if not self.deterministic_gates:
            raise RouteExecutionContractError("route verification gates are required")

    def permits(self, component: RouteComponent) -> bool:
        return component not in self.forbidden_components

    @property
    def fingerprint(self) -> str:
        payload = {
            "mode": self.mode.value,
            "candidate_owner": self.candidate_owner.value,
            "expected_outcome": self.expected_outcome.value,
            "required": [item.value for item in self.required_components],
            "conditional": [item.value for item in self.conditional_components],
            "forbidden": [item.value for item in self.forbidden_components],
            "verification_profile": self.verification_profile,
            "deterministic_gates": list(self.deterministic_gates),
            "policy_version": self.policy_version,
            "route_policy_version": self.route_policy_version,
            "input_fingerprint": self.input_fingerprint,
            "missing_inputs": list(self.missing_inputs),
            "reason_codes": list(self.reason_codes),
            "risk": self.risk,
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "candidate_owner": self.candidate_owner.value,
            "expected_outcome": self.expected_outcome.value,
            "required_components": [item.value for item in self.required_components],
            "conditional_components": [item.value for item in self.conditional_components],
            "forbidden_components": [item.value for item in self.forbidden_components],
            "verification_profile": self.verification_profile,
            "deterministic_gates": list(self.deterministic_gates),
            "policy_version": self.policy_version,
            "route_policy_version": self.route_policy_version,
            "input_fingerprint": self.input_fingerprint,
            "missing_inputs": list(self.missing_inputs),
            "reason_codes": list(self.reason_codes),
            "risk": self.risk,
            "fingerprint": self.fingerprint,
        }


class RouteExecutionPolicy:
    """Own which components may run after RouteDecision; adapters only execute it."""

    version = "route-execution-policy-v1"

    def plan(
        self,
        route: RouteDecision,
        verification: VerificationProfileContract,
    ) -> RouteExecutionContract:
        owner, outcome, required, conditional = self._shape(route)
        if (
            verification.semantic_policy is SemanticVerifierPolicy.CONDITIONAL
            and RouteComponent.SEMANTIC_VERIFIER not in conditional
        ):
            raise RouteExecutionContractError(
                "conditional verification profile must expose semantic verifier"
            )
        if (
            verification.semantic_policy is SemanticVerifierPolicy.FORBIDDEN
            and RouteComponent.SEMANTIC_VERIFIER not in (set(RouteComponent) - required - conditional)
        ):
            raise RouteExecutionContractError(
                "forbidden verification profile cannot permit semantic verifier"
            )
        forbidden = tuple(
            component for component in RouteComponent
            if component not in required and component not in conditional
        )
        return RouteExecutionContract(
            mode=route.mode,
            candidate_owner=owner,
            expected_outcome=outcome,
            required_components=tuple(component for component in RouteComponent if component in required),
            conditional_components=tuple(
                component for component in RouteComponent if component in conditional
            ),
            forbidden_components=forbidden,
            verification_profile=verification.profile.value,
            deterministic_gates=verification.deterministic_gates,
            policy_version=self.version,
            route_policy_version=route.policy_version,
            input_fingerprint=route.input_fingerprint,
            missing_inputs=route.missing_inputs,
            reason_codes=route.reason_codes,
            risk=route.risk.value,
        )

    def plan_command_primary(
        self,
        route: RouteDecisionV2,
        verification: VerificationProfileContract,
        *,
        input_fingerprint: str,
    ) -> RouteExecutionContract:
        """Compile the first command-primary path directly from its route."""

        if route.mode is not RouteMode.KNOWLEDGE_QA:
            raise RouteExecutionContractError(
                "only the knowledge command-primary path is enabled"
            )
        owner, outcome, required, conditional = self._shape(route)
        forbidden = tuple(
            component for component in RouteComponent
            if component not in required and component not in conditional
        )
        return RouteExecutionContract(
            mode=route.mode,
            candidate_owner=owner,
            expected_outcome=outcome,
            required_components=tuple(
                component for component in RouteComponent if component in required
            ),
            conditional_components=tuple(
                component for component in RouteComponent if component in conditional
            ),
            forbidden_components=forbidden,
            verification_profile=verification.profile.value,
            deterministic_gates=verification.deterministic_gates,
            policy_version=self.version,
            route_policy_version=route.policy_version,
            input_fingerprint=input_fingerprint,
            reason_codes=(route.reason_code,),
            risk=route.risk.value,
        )

    @staticmethod
    def _shape(route: RouteDecision | RouteDecisionV2):
        mode = route.mode
        turn = {RouteComponent.TURN_RECORD}
        semantic = {RouteComponent.SEMANTIC_VERIFIER}
        if mode is RouteMode.DIRECT or mode is RouteMode.OUT_OF_SCOPE:
            return (
                CandidateOwner.RULE_POLICY, RouteExpectedOutcome.COMPLETED,
                turn | {RouteComponent.RULE_RESPONSE}, set(),
            )
        if mode is RouteMode.CLARIFY:
            return (
                CandidateOwner.RULE_POLICY, RouteExpectedOutcome.NEEDS_INPUT,
                turn | {RouteComponent.RULE_RESPONSE, RouteComponent.MISSING_INPUT_SIGNAL},
                set(),
            )
        if mode is RouteMode.KNOWLEDGE_QA:
            return (
                CandidateOwner.GROUNDED_ANSWER_GENERATOR,
                RouteExpectedOutcome.COMPLETED,
                turn | {
                    RouteComponent.PRE_ROUTE_RETRIEVER,
                    RouteComponent.GROUNDED_ANSWER_GENERATOR,
                    RouteComponent.REQUIREMENT_COVERAGE_GATE,
                    RouteComponent.CITATION_CLAIM_GATE,
                }, semantic,
            )
        if mode is RouteMode.AGENT_TASK:
            required_tools = (
                {RouteComponent.BUSINESS_TOOL}
                if any(authority is not RequiredAuthority.KNOWLEDGE
                       for authority in route.required_authorities)
                else set()
            )
            return (
                CandidateOwner.AGENT, RouteExpectedOutcome.COMPLETED,
                turn | {
                    RouteComponent.AGENT_ORCHESTRATOR,
                    RouteComponent.REQUIREMENT_COVERAGE_GATE,
                } | required_tools, semantic | {
                    RouteComponent.AGENT_KNOWLEDGE_TOOL,
                    *(set() if required_tools else {RouteComponent.BUSINESS_TOOL}),
                },
            )
        if mode is RouteMode.MIXED:
            return (
                CandidateOwner.MIXED_AUTHORITY_AGENT, RouteExpectedOutcome.COMPLETED,
                turn | {
                    RouteComponent.PRE_ROUTE_RETRIEVER,
                    RouteComponent.AGENT_ORCHESTRATOR,
                    RouteComponent.BUSINESS_TOOL,
                    RouteComponent.REQUIREMENT_COVERAGE_GATE,
                    RouteComponent.CITATION_CLAIM_GATE,
                }, semantic | {RouteComponent.AGENT_KNOWLEDGE_TOOL},
            )
        if mode is RouteMode.MULTI_DOMAIN:
            return (
                CandidateOwner.TASK_GRAPH, RouteExpectedOutcome.COMPLETED,
                turn | {
                    RouteComponent.AGENT_ORCHESTRATOR,
                    RouteComponent.TASK_GRAPH,
                    RouteComponent.REQUIREMENT_COVERAGE_GATE,
                    RouteComponent.BUSINESS_TOOL,
                }, semantic | {
                    RouteComponent.AGENT_KNOWLEDGE_TOOL,
                    RouteComponent.CONDITIONAL_SYNTHESIS,
                },
            )
        if mode is RouteMode.HANDOFF:
            return (
                CandidateOwner.HANDOFF_DRAFT, RouteExpectedOutcome.HANDOFF_DRAFT,
                turn | {RouteComponent.HANDOFF_DRAFT}, set(),
            )
        raise RouteExecutionContractError(f"unsupported route mode: {mode!r}")
