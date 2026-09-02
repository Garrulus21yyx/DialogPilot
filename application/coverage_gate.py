"""Requirement-level coverage and deterministic verification-profile policy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from application.authority_policy import (
    AuthorityPolicyRegistry,
    AuthoritySupport,
    FactRequirement,
    RequirementEffect,
)
from application.evidence_receipt import (
    EvidenceKind,
    EvidenceReceipt,
    EvidenceReceiptIssuer,
    EvidenceReceiptVerifier,
    EvidenceResolver,
    RequirementStatus,
)
from application.route_decision import RouteDecision, RouteMode


class CoverageContractError(ValueError):
    pass


@dataclass(frozen=True)
class ClaimBinding:
    requirement_id: str
    evidence_receipt_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.requirement_id or not self.evidence_receipt_ids:
            raise CoverageContractError("claim binding requires evidence references")
        if len(set(self.evidence_receipt_ids)) != len(self.evidence_receipt_ids):
            raise CoverageContractError("claim binding contains duplicate references")


@dataclass(frozen=True)
class RequirementCoverage:
    requirement_id: str
    status: RequirementStatus
    evidence_receipt_ids: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True)
class RequirementCoverageReport:
    complete: bool
    requirements: tuple[RequirementCoverage, ...]
    duplicate_evidence_ids: tuple[str, ...]
    unexpected_evidence_ids: tuple[str, ...]
    invalid_evidence_refs: tuple[str, ...]

    def ids_for(self, status: RequirementStatus) -> tuple[str, ...]:
        return tuple(
            item.requirement_id for item in self.requirements
            if item.status is status
        )

    def to_dict(self) -> dict[str, Any]:
        by_status = {
            status.value.lower(): list(self.ids_for(status))
            for status in RequirementStatus
        }
        return {
            "complete": self.complete,
            **by_status,
            "requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "status": item.status.value,
                    "evidence_receipt_ids": list(item.evidence_receipt_ids),
                    "reason_code": item.reason_code,
                }
                for item in self.requirements
            ],
            "duplicate_evidence_ids": list(self.duplicate_evidence_ids),
            "unexpected_evidence_ids": list(self.unexpected_evidence_ids),
            "invalid_evidence_refs": list(self.invalid_evidence_refs),
        }


KnowledgeRevisionValidator = Callable[[EvidenceReceipt], RequirementStatus]


class RequirementCoverageGate:
    """Evaluate evidence against requirements; task success is not an input."""

    def __init__(self, policies: AuthorityPolicyRegistry):
        self._policies = policies
        self._issuer = EvidenceReceiptIssuer(policies)
        self._verifier = EvidenceReceiptVerifier()

    def evaluate(
        self,
        requirements: Sequence[FactRequirement],
        receipt_values: Sequence[EvidenceReceipt | Mapping[str, Any]],
        *,
        resolvers: Mapping[str, EvidenceResolver],
        claim_bindings: Sequence[ClaimBinding] = (),
        knowledge_revision_validator: KnowledgeRevisionValidator | None = None,
        now: datetime | None = None,
    ) -> RequirementCoverageReport:
        current = now or datetime.now(timezone.utc)
        requirement_by_id = {
            item.requirement_id: item for item in requirements
        }
        if len(requirement_by_id) != len(requirements):
            raise CoverageContractError("requirements must be unique")
        claims = {item.requirement_id: item for item in claim_bindings}
        if len(claims) != len(claim_bindings):
            raise CoverageContractError("claim bindings must be unique")

        restored: list[EvidenceReceipt] = []
        invalid_refs: list[str] = []
        invalid_requirement_ids: set[str] = set()
        for index, value in enumerate(receipt_values):
            if isinstance(value, EvidenceReceipt):
                restored.append(value)
                continue
            try:
                restored.append(self._issuer.restore(value))
            except Exception:
                invalid_refs.append(str(value.get("receipt_id") or f"wire:{index}"))
                requirement_id = str(value.get("requirement_id") or "")
                if requirement_id in requirement_by_id:
                    invalid_requirement_ids.add(requirement_id)

        ids = [item.receipt_id for item in restored]
        duplicate_ids = tuple(sorted({item for item in ids if ids.count(item) > 1}))
        unexpected_ids = tuple(sorted(
            item.receipt_id for item in restored
            if item.requirement_id not in requirement_by_id
        ))
        by_requirement: dict[str, list[EvidenceReceipt]] = {}
        for receipt in restored:
            if receipt.receipt_id in duplicate_ids:
                continue
            if receipt.requirement_id in requirement_by_id:
                by_requirement.setdefault(receipt.requirement_id, []).append(receipt)

        known_receipt_ids = set(ids)
        for claim in claim_bindings:
            if claim.requirement_id not in requirement_by_id:
                invalid_refs.extend(claim.evidence_receipt_ids)
            invalid_refs.extend(
                receipt_id for receipt_id in claim.evidence_receipt_ids
                if receipt_id not in known_receipt_ids
            )

        coverage = tuple(
            self._evaluate_requirement(
                requirement,
                tuple(by_requirement.get(requirement.requirement_id, ())),
                resolver_by_producer=resolvers,
                claim=claims.get(requirement.requirement_id),
                knowledge_revision_validator=knowledge_revision_validator,
                has_invalid_wire=(
                    requirement.requirement_id in invalid_requirement_ids
                ),
                now=current,
            )
            for requirement in requirements
        )
        invalid_tuple = tuple(sorted(set(invalid_refs)))
        complete = (
            bool(coverage)
            and all(item.status is RequirementStatus.SATISFIED for item in coverage)
            and not duplicate_ids
            and not unexpected_ids
            and not invalid_tuple
        )
        return RequirementCoverageReport(
            complete, coverage, duplicate_ids, unexpected_ids, invalid_tuple
        )

    def _evaluate_requirement(
        self,
        requirement: FactRequirement,
        receipts: tuple[EvidenceReceipt, ...],
        *,
        resolver_by_producer: Mapping[str, EvidenceResolver],
        claim: ClaimBinding | None,
        knowledge_revision_validator: KnowledgeRevisionValidator | None,
        has_invalid_wire: bool,
        now: datetime,
    ) -> RequirementCoverage:
        if requirement.support is AuthoritySupport.UNSUPPORTED:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.UNSUPPORTED, (),
                "AUTHORITY_UNSUPPORTED",
            )
        if has_invalid_wire:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.INVALID_EVIDENCE,
                (), "WIRE_EVIDENCE_INVALID",
            )
        if not receipts:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.MISSING, (),
                "EVIDENCE_MISSING",
            )
        statuses: list[tuple[EvidenceReceipt, RequirementStatus]] = []
        for receipt in receipts:
            resolver = resolver_by_producer.get(receipt.producer_id)
            if resolver is None:
                status = RequirementStatus.INVALID_EVIDENCE
            else:
                status = self._verifier.verify(receipt, resolver, now=now)
            if (
                status is RequirementStatus.SATISFIED
                and receipt.kind is EvidenceKind.KNOWLEDGE
            ):
                status = (
                    knowledge_revision_validator(receipt)
                    if knowledge_revision_validator is not None
                    else RequirementStatus.INVALID_EVIDENCE
                )
            statuses.append((receipt, status))

        receipt_ids = tuple(item.receipt_id for item, _status in statuses)
        status_values = [status for _receipt, status in statuses]
        if RequirementStatus.INVALID_EVIDENCE in status_values:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.INVALID_EVIDENCE,
                receipt_ids, "EVIDENCE_INVALID",
            )
        if RequirementStatus.CONFLICTING in status_values:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.CONFLICTING,
                receipt_ids, "PRODUCER_CONFLICT",
            )
        satisfied = [
            receipt for receipt, status in statuses
            if status is RequirementStatus.SATISFIED
        ]
        if (
            len({item.content_sha256 for item in satisfied}) > 1
            and requirement.effect is RequirementEffect.WRITE
            or (
                len({item.content_sha256 for item in satisfied}) > 1
                and any(item.kind is EvidenceKind.BUSINESS_TOOL for item in satisfied)
            )
        ):
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.CONFLICTING,
                receipt_ids, "MULTIPLE_AUTHORITATIVE_VALUES",
            )
        if satisfied and requirement.effect is RequirementEffect.WRITE:
            bound_ids = set(claim.evidence_receipt_ids) if claim else set()
            if not bound_ids.intersection(item.receipt_id for item in satisfied):
                return RequirementCoverage(
                    requirement.requirement_id, RequirementStatus.MISSING,
                    receipt_ids, "ACTION_CLAIM_RECEIPT_NOT_BOUND",
                )
        if satisfied:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.SATISFIED,
                tuple(item.receipt_id for item in satisfied),
                "REQUIREMENT_SATISFIED",
            )
        if RequirementStatus.STALE in status_values:
            return RequirementCoverage(
                requirement.requirement_id, RequirementStatus.STALE,
                receipt_ids, "EVIDENCE_STALE",
            )
        return RequirementCoverage(
            requirement.requirement_id, RequirementStatus.MISSING,
            receipt_ids, "NO_SATISFYING_EVIDENCE",
        )


class VerificationProfile(str, Enum):
    RULE_ONLY = "RULE_ONLY"
    GROUNDED_KNOWLEDGE = "GROUNDED_KNOWLEDGE"
    AUTHORITATIVE_RECEIPT = "AUTHORITATIVE_RECEIPT"
    MIXED_AUTHORITY = "MIXED_AUTHORITY"
    MULTI_TASK = "MULTI_TASK"
    HANDOFF_CONTRACT = "HANDOFF_CONTRACT"


class SemanticVerifierPolicy(str, Enum):
    FORBIDDEN = "FORBIDDEN"
    CONDITIONAL = "CONDITIONAL"


class SemanticVerifierStatus(str, Enum):
    INVOKED = "INVOKED"
    AVOIDED = "AVOIDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class VerificationProfileContract:
    profile: VerificationProfile
    deterministic_gates: tuple[str, ...]
    semantic_policy: SemanticVerifierPolicy
    policy_version: str = "verification-profile-v1"


@dataclass(frozen=True)
class VerificationInvocationDecision:
    contract: VerificationProfileContract
    semantic_status: SemanticVerifierStatus
    reason_code: str


class VerificationProfileRegistry:
    version = "verification-profile-registry-v1"

    def decide(
        self,
        route: RouteDecision,
        requirements: Sequence[FactRequirement],
        *,
        semantic_ambiguity: bool,
        semantic_verifier_available: bool,
    ) -> VerificationInvocationDecision:
        contract = self._contract(route.mode, requirements)
        if contract.semantic_policy is SemanticVerifierPolicy.FORBIDDEN:
            return VerificationInvocationDecision(
                contract, SemanticVerifierStatus.NOT_APPLICABLE,
                "SEMANTIC_VERIFIER_FORBIDDEN",
            )
        if not semantic_ambiguity:
            return VerificationInvocationDecision(
                contract, SemanticVerifierStatus.AVOIDED,
                "DETERMINISTIC_GATES_SUFFICIENT",
            )
        if not semantic_verifier_available:
            return VerificationInvocationDecision(
                contract, SemanticVerifierStatus.UNAVAILABLE,
                "REQUIRED_SEMANTIC_VERIFIER_UNAVAILABLE",
            )
        return VerificationInvocationDecision(
            contract, SemanticVerifierStatus.INVOKED,
            "SEMANTIC_AMBIGUITY_REQUIRES_VERIFIER",
        )

    def contract_for(
        self,
        mode: RouteMode,
        requirements: Sequence[FactRequirement],
    ) -> VerificationProfileContract:
        """Return the authoritative deterministic gate/profile contract for a route."""
        return self._contract(mode, requirements)

    @staticmethod
    def _contract(
        mode: RouteMode,
        requirements: Sequence[FactRequirement],
    ) -> VerificationProfileContract:
        receipt_gate = "REQUIREMENT_RECEIPT_GATE"
        citation_gate = "CLAIM_CITATION_GATE"
        if mode in {RouteMode.DIRECT, RouteMode.OUT_OF_SCOPE, RouteMode.CLARIFY}:
            return VerificationProfileContract(
                VerificationProfile.RULE_ONLY, ("ROUTE_POLICY_GATE",),
                SemanticVerifierPolicy.FORBIDDEN,
            )
        if mode is RouteMode.HANDOFF:
            return VerificationProfileContract(
                VerificationProfile.HANDOFF_CONTRACT,
                ("HANDOFF_BINDING_GATE",), SemanticVerifierPolicy.FORBIDDEN,
            )
        if mode is RouteMode.KNOWLEDGE_QA:
            return VerificationProfileContract(
                VerificationProfile.GROUNDED_KNOWLEDGE,
                (receipt_gate, "ACTIVE_SOURCE_REVISION_GATE", citation_gate),
                SemanticVerifierPolicy.CONDITIONAL,
            )
        if mode is RouteMode.MIXED:
            return VerificationProfileContract(
                VerificationProfile.MIXED_AUTHORITY,
                (receipt_gate, "ACTIVE_SOURCE_REVISION_GATE", citation_gate),
                SemanticVerifierPolicy.CONDITIONAL,
            )
        if mode is RouteMode.MULTI_DOMAIN:
            return VerificationProfileContract(
                VerificationProfile.MULTI_TASK,
                (receipt_gate, "DEPENDENCY_INPUT_GATE", citation_gate),
                SemanticVerifierPolicy.CONDITIONAL,
            )
        if any(item.effect is RequirementEffect.WRITE for item in requirements):
            return VerificationProfileContract(
                VerificationProfile.AUTHORITATIVE_RECEIPT,
                (receipt_gate, "COMMITTED_EFFECT_GATE"),
                SemanticVerifierPolicy.FORBIDDEN,
            )
        return VerificationProfileContract(
            VerificationProfile.AUTHORITATIVE_RECEIPT,
            (receipt_gate, citation_gate), SemanticVerifierPolicy.CONDITIONAL,
        )
