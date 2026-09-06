"""Requirement coverage, evidence authority and claim binding."""
from datetime import datetime, timezone

import pytest

from application.authority_policy import AuthorityPolicyRegistry
from application.coverage_gate import (
    ClaimBinding,
    RequirementCoverageGate,
)
from application.evidence_receipt import (
    ActionReceiptLocator,
    BusinessToolLocator,
    EvidenceReceiptIssuer,
    KnowledgeLocator,
    RequirementStatus,
)
from application.hybrid_retrieval import RetrievalStatus
from mcp.tool_manager import ToolCallStatus, ToolEffectStatus


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 64


class Resolver:
    def __init__(self, payload):
        self.payload = payload

    def resolve(self, locator):
        assert locator is not None
        return self.payload


def _order_receipt():
    payload = {
        "order_id": "order-1", "status": "shipped", "version": 3,
        "updated_at": NOW.isoformat(),
    }
    receipt = EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1()).adapter(
        "business-tool-evidence-adapter", "business-tool-evidence-adapter-v1"
    ).issue(
        requirement_id="order.current_state",
        producer_id="order_lookup",
        producer_version="order-view-v2",
        locator=BusinessToolLocator(
            "call-order", "order_lookup", "order-1", "3"
        ),
        status=ToolCallStatus.SUCCESS,
        observed_at=NOW,
        payload=payload,
    )
    return receipt, payload


def _knowledge_receipt():
    payload = {
        "source_id": "source-1", "source_revision": "revision-1",
        "checksum": SHA, "content": "policy",
    }
    receipt = EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1()).adapter(
        "knowledge-evidence-adapter", "knowledge-evidence-adapter-v1"
    ).issue(
        requirement_id="knowledge.active_source",
        producer_id="knowledge_search",
        producer_version="knowledge-evidence-pack-result-v1",
        locator=KnowledgeLocator(
            "tenant-1", "backend-1", "generation-1", "public", "zh-CN",
            None, "source-1", "revision-1", SHA, 0, 6
        ),
        status=RetrievalStatus.OK,
        observed_at=NOW,
        payload=payload,
    )
    return receipt, payload


def _action_receipt():
    payload = {
        "refund_id": "refund-1", "receipt_id": "refund-1",
        "order_id": "order-1", "status": "requested",
    }
    receipt = EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1()).adapter(
        "action-receipt-evidence-adapter", "action-receipt-evidence-adapter-v1"
    ).issue(
        requirement_id="refund.request_action",
        producer_id="refund_request_create",
        producer_version="refund-request-result-v1",
        locator=ActionReceiptLocator(
            "call-refund", "refund_request_create", "refund-1"
        ),
        status=ToolEffectStatus.COMMITTED,
        observed_at=NOW,
        payload=payload,
    )
    return receipt, payload


def test_plain_worker_text_or_task_success_cannot_satisfy_dynamic_requirement():
    policies = AuthorityPolicyRegistry.v1()
    report = RequirementCoverageGate(policies).evaluate(
        (policies.get("order.current_state"),), (), resolvers={}, now=NOW
    )

    assert report.complete is False
    assert report.ids_for(RequirementStatus.MISSING) == ("order.current_state",)


def test_supported_account_authority_without_receipt_is_missing_evidence():
    policies = AuthorityPolicyRegistry.v1()
    report = RequirementCoverageGate(policies).evaluate(
        (policies.get("account.current_state"),), (), resolvers={}, now=NOW
    )
    assert report.ids_for(RequirementStatus.MISSING) == (
        "account.current_state",
    )
    assert report.ids_for(RequirementStatus.UNSUPPORTED) == ()


def test_valid_authoritative_tool_receipt_satisfies_required_fields():
    policies = AuthorityPolicyRegistry.v1()
    receipt, payload = _order_receipt()
    report = RequirementCoverageGate(policies).evaluate(
        (policies.get("order.current_state"),), (receipt,),
        resolvers={"order_lookup": Resolver(payload)}, now=NOW,
    )

    assert report.complete is True
    assert report.ids_for(RequirementStatus.SATISFIED) == ("order.current_state",)


def test_malformed_wire_receipt_is_invalid_not_silently_missing():
    policies = AuthorityPolicyRegistry.v1()
    receipt, _payload = _order_receipt()
    malformed = receipt.to_dict()
    malformed["content_sha256"] = "b" * 64

    report = RequirementCoverageGate(policies).evaluate(
        (policies.get("order.current_state"),), (malformed,),
        resolvers={}, now=NOW,
    )

    assert report.ids_for(RequirementStatus.INVALID_EVIDENCE) == (
        "order.current_state",
    )
    assert report.invalid_evidence_refs == (receipt.receipt_id,)


def test_mixed_coverage_is_partial_when_policy_or_state_evidence_is_absent():
    policies = AuthorityPolicyRegistry.v1()
    order, payload = _order_receipt()
    report = RequirementCoverageGate(policies).evaluate(
        (
            policies.get("knowledge.active_source"),
            policies.get("order.current_state"),
        ),
        (order,), resolvers={"order_lookup": Resolver(payload)}, now=NOW,
    )

    assert report.complete is False
    assert report.ids_for(RequirementStatus.SATISFIED) == ("order.current_state",)
    assert report.ids_for(RequirementStatus.MISSING) == ("knowledge.active_source",)


def test_duplicate_and_unexpected_evidence_are_reported_and_block_completion():
    policies = AuthorityPolicyRegistry.v1()
    order, payload = _order_receipt()
    knowledge, knowledge_payload = _knowledge_receipt()
    report = RequirementCoverageGate(policies).evaluate(
        (policies.get("order.current_state"),),
        (order, order, knowledge),
        resolvers={
            "order_lookup": Resolver(payload),
            "knowledge_search": Resolver(knowledge_payload),
        },
        now=NOW,
    )

    assert report.complete is False
    assert report.duplicate_evidence_ids == (order.receipt_id,)
    assert report.unexpected_evidence_ids == (knowledge.receipt_id,)


def test_write_claim_requires_a_bound_committed_receipt():
    policies = AuthorityPolicyRegistry.v1()
    receipt, payload = _action_receipt()
    requirement = policies.get("refund.request_action")
    unbound = RequirementCoverageGate(policies).evaluate(
        (requirement,), (receipt,),
        resolvers={"refund_request_create": Resolver(payload)}, now=NOW,
    )
    bound = RequirementCoverageGate(policies).evaluate(
        (requirement,), (receipt,),
        resolvers={"refund_request_create": Resolver(payload)},
        claim_bindings=(ClaimBinding(
            requirement.requirement_id, (receipt.receipt_id,)
        ),),
        now=NOW,
    )

    assert unbound.ids_for(RequirementStatus.MISSING) == (
        "refund.request_action",
    )
    assert bound.complete is True


def test_knowledge_requires_active_revision_validator_from_t04a():
    policies = AuthorityPolicyRegistry.v1()
    receipt, payload = _knowledge_receipt()
    kwargs = dict(
        requirements=(policies.get("knowledge.active_source"),),
        receipt_values=(receipt,),
        resolvers={"knowledge_search": Resolver(payload)},
        now=NOW,
    )
    without_active_revision = RequirementCoverageGate(policies).evaluate(**kwargs)
    with_active_revision = RequirementCoverageGate(policies).evaluate(
        **kwargs,
        knowledge_revision_validator=lambda _receipt: RequirementStatus.SATISFIED,
    )

    assert without_active_revision.ids_for(RequirementStatus.INVALID_EVIDENCE) == (
        "knowledge.active_source",
    )
    assert with_active_revision.complete is True
