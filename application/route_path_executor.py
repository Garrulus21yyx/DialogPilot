"""Execute one RouteExecutionContract without guessing route or authority semantics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from application.route_execution import (
    CandidateOwner,
    RouteComponent,
    RouteExecutionContract,
    RouteExpectedOutcome,
)


class RoutePathError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RouteCandidate:
    content: str
    owner: CandidateOwner
    component_receipts: tuple[RouteComponent, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise RoutePathError("EMPTY_CANDIDATE", "route candidate must not be empty")
        if len(set(self.component_receipts)) != len(self.component_receipts):
            raise RoutePathError("DUPLICATE_COMPONENT_RECEIPT", "component receipts must be unique")


@dataclass(frozen=True)
class DeterministicGateResult:
    publishable: bool
    semantic_ambiguity: bool = False
    reason_code: str = ""


@dataclass(frozen=True)
class RoutePathResult:
    expected_outcome: RouteExpectedOutcome
    candidate: RouteCandidate
    publishable: bool
    invocation_trace: tuple[RouteComponent, ...]
    reason_code: str


@dataclass(frozen=True)
class RoutePathOperations:
    rule_candidate: Callable[[RouteExecutionContract], Awaitable[RouteCandidate]]
    retrieve: Callable[[RouteExecutionContract], Awaitable[Any]]
    grounded_generate: Callable[[RouteExecutionContract, Any], Awaitable[RouteCandidate]]
    run_agent: Callable[
        [RouteExecutionContract, Optional[Any]], Awaitable[RouteCandidate]
    ]
    handoff_draft: Callable[[RouteExecutionContract], Awaitable[RouteCandidate]]
    deterministic_gate: Callable[
        [RouteExecutionContract, RouteCandidate], Awaitable[DeterministicGateResult]
    ]
    semantic_verifier: Callable[
        [RouteExecutionContract, RouteCandidate], Awaitable[bool]
    ]
    record_turn: Callable[
        [RouteExecutionContract, RouteCandidate, bool], Awaitable[None]
    ]


class RoutePathExecutor:
    """Application path selector; domain work remains behind Agent/Knowledge ports."""

    version = "route-path-executor-v1"

    async def execute(
        self,
        contract: RouteExecutionContract,
        operations: RoutePathOperations,
    ) -> RoutePathResult:
        invoked: list[RouteComponent] = []
        evidence: Any = None

        if contract.candidate_owner is CandidateOwner.RULE_POLICY:
            candidate = await operations.rule_candidate(contract)
            invoked.append(RouteComponent.RULE_RESPONSE)
            if contract.expected_outcome is RouteExpectedOutcome.NEEDS_INPUT:
                invoked.append(RouteComponent.MISSING_INPUT_SIGNAL)
        elif contract.candidate_owner is CandidateOwner.GROUNDED_ANSWER_GENERATOR:
            evidence = await operations.retrieve(contract)
            invoked.append(RouteComponent.PRE_ROUTE_RETRIEVER)
            candidate = await operations.grounded_generate(contract, evidence)
            invoked.append(RouteComponent.GROUNDED_ANSWER_GENERATOR)
        elif contract.candidate_owner in {
            CandidateOwner.AGENT,
            CandidateOwner.MIXED_AUTHORITY_AGENT,
            CandidateOwner.TASK_GRAPH,
        }:
            if contract.candidate_owner is CandidateOwner.MIXED_AUTHORITY_AGENT:
                evidence = await operations.retrieve(contract)
                invoked.append(RouteComponent.PRE_ROUTE_RETRIEVER)
            candidate = await operations.run_agent(contract, evidence)
            invoked.append(RouteComponent.AGENT_ORCHESTRATOR)
            if contract.candidate_owner is CandidateOwner.TASK_GRAPH:
                invoked.append(RouteComponent.TASK_GRAPH)
        elif contract.candidate_owner is CandidateOwner.HANDOFF_DRAFT:
            candidate = await operations.handoff_draft(contract)
            invoked.append(RouteComponent.HANDOFF_DRAFT)
        else:
            raise RoutePathError(
                "UNSUPPORTED_CANDIDATE_OWNER",
                f"unsupported candidate owner: {contract.candidate_owner.value}",
            )

        if candidate.owner is not contract.candidate_owner:
            raise RoutePathError(
                "CANDIDATE_OWNER_MISMATCH",
                f"expected {contract.candidate_owner.value}, got {candidate.owner.value}",
            )
        invoked.extend(candidate.component_receipts)
        self._validate_invocations(contract, invoked)

        gate = await operations.deterministic_gate(contract, candidate)
        for component in (
            RouteComponent.REQUIREMENT_COVERAGE_GATE,
            RouteComponent.CITATION_CLAIM_GATE,
        ):
            if component in contract.required_components:
                invoked.append(component)
        publishable = gate.publishable
        reason_code = gate.reason_code or (
            "DETERMINISTIC_GATES_PASSED" if publishable else "DETERMINISTIC_GATES_REJECTED"
        )
        if publishable and gate.semantic_ambiguity:
            if RouteComponent.SEMANTIC_VERIFIER not in contract.conditional_components:
                raise RoutePathError(
                    "FORBIDDEN_SEMANTIC_VERIFIER",
                    "route profile forbids semantic verifier",
                )
            invoked.append(RouteComponent.SEMANTIC_VERIFIER)
            publishable = await operations.semantic_verifier(contract, candidate)
            reason_code = (
                "SEMANTIC_VERIFIER_PASSED"
                if publishable else "SEMANTIC_VERIFIER_REJECTED"
            )

        invoked.append(RouteComponent.TURN_RECORD)
        self._validate_invocations(contract, invoked, final=True)
        await operations.record_turn(contract, candidate, publishable)
        return RoutePathResult(
            expected_outcome=contract.expected_outcome,
            candidate=candidate,
            publishable=publishable,
            invocation_trace=tuple(invoked),
            reason_code=reason_code,
        )

    @staticmethod
    def _validate_invocations(
        contract: RouteExecutionContract,
        invoked: list[RouteComponent],
        *,
        final: bool = False,
    ) -> None:
        duplicates = sorted({item.value for item in invoked if invoked.count(item) > 1})
        if duplicates:
            raise RoutePathError(
                "DUPLICATE_COMPONENT_INVOCATION",
                f"components invoked more than once: {duplicates}",
            )
        forbidden = [item.value for item in invoked if item in contract.forbidden_components]
        if forbidden:
            raise RoutePathError(
                "FORBIDDEN_COMPONENT_INVOCATION",
                f"route invoked forbidden components: {forbidden}",
            )
        if final:
            missing = [
                item.value for item in contract.required_components
                if item not in invoked
            ]
            if missing:
                raise RoutePathError(
                    "MISSING_REQUIRED_COMPONENT",
                    f"route omitted required components: {missing}",
                )


def agent_route_candidate(
    contract: RouteExecutionContract,
    result: Any,
) -> RouteCandidate:
    """Project native AgentOutcome tool receipts; audit records are not an input."""
    components: list[RouteComponent] = []
    evidence_refs: list[str] = []
    for outcome in getattr(result, "agent_outcomes", ()) or ():
        for receipt in outcome.get("tool_receipts", ()) or ():
            if str(receipt.get("status") or "") != "success":
                continue
            authority = str(receipt.get("authority") or "").strip()
            schema = str(receipt.get("output_schema_version") or "").strip()
            if not authority or not schema:
                continue
            tool_name = str(receipt.get("tool_name") or "")
            component = (
                RouteComponent.AGENT_KNOWLEDGE_TOOL
                if "knowledge" in authority.casefold()
                or tool_name == "knowledge_search"
                else RouteComponent.BUSINESS_TOOL
            )
            if component not in components:
                components.append(component)
            receipt_id = str(receipt.get("receipt_id") or "").strip()
            if receipt_id and receipt_id not in evidence_refs:
                evidence_refs.append(receipt_id)
    return RouteCandidate(
        content=str(getattr(result, "response", "") or ""),
        owner=contract.candidate_owner,
        component_receipts=tuple(components),
        evidence_refs=tuple(evidence_refs),
    )
