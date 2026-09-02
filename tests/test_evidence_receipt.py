"""M2-T03 canonical evidence schema, issuance, and revalidation contracts."""
from datetime import datetime, timedelta, timezone

import pytest

from application.authority_policy import (
    AuthorityContractError,
    AuthorityPolicyRegistry,
)
from application.evidence_receipt import (
    ActionReceiptLocator,
    BusinessToolLocator,
    CommitmentEvidenceStatus,
    CommitmentLocator,
    EvidenceContractError,
    EvidenceKind,
    EvidenceReceiptIssuer,
    EvidenceReceiptVerifier,
    EvidenceSchema,
    HumanAssertionLocator,
    HumanAssertionStatus,
    KnowledgeLocator,
    MediaObservationLocator,
    MediaObservationStatus,
    MemoryEventLocator,
    MemoryEventStatus,
    RequirementStatus,
)
from application.hybrid_retrieval import RetrievalStatus
from mcp.tool_manager import ToolCallStatus, ToolEffectStatus


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
SHA = "a" * 64


@pytest.mark.parametrize(
    ("kind", "locator", "status"),
    [
        (
            EvidenceKind.KNOWLEDGE,
            KnowledgeLocator("generation-1", "source-1", "revision-1", SHA, 0, 10),
            RetrievalStatus.OK,
        ),
        (
            EvidenceKind.BUSINESS_TOOL,
            BusinessToolLocator("call-1", "order_lookup", "order-1", "3"),
            ToolCallStatus.SUCCESS,
        ),
        (
            EvidenceKind.ACTION_RECEIPT,
            ActionReceiptLocator("call-2", "refund_request_create", "refund-1"),
            ToolEffectStatus.COMMITTED,
        ),
        (
            EvidenceKind.MEMORY_EVENT,
            MemoryEventLocator("memory-1", "conversation-1", 7),
            MemoryEventStatus.OBSERVED,
        ),
        (
            EvidenceKind.COMMITMENT,
            CommitmentLocator("commitment-1", 2),
            CommitmentEvidenceStatus.ACTIVE,
        ),
        (
            EvidenceKind.MEDIA_OBSERVATION,
            MediaObservationLocator("asset-1", SHA, "page:1"),
            MediaObservationStatus.OBSERVED,
        ),
        (
            EvidenceKind.HUMAN_ASSERTION,
            HumanAssertionLocator("assertion-1", "principal-1", "event-1"),
            HumanAssertionStatus.ASSERTED,
        ),
    ],
)
def test_each_evidence_kind_has_a_closed_locator_and_status_schema(
    kind, locator, status,
):
    EvidenceSchema.validate(kind, locator, status)


def test_kind_status_and_locator_domains_cannot_be_mixed():
    with pytest.raises(EvidenceContractError, match="status domain mismatch"):
        EvidenceSchema.validate(
            EvidenceKind.BUSINESS_TOOL,
            BusinessToolLocator("call-1", "order_lookup", "order-1", "3"),
            RetrievalStatus.OK,
        )
    with pytest.raises(EvidenceContractError, match="locator kind mismatch"):
        EvidenceSchema.validate(
            EvidenceKind.KNOWLEDGE,
            MemoryEventLocator("memory-1", "conversation-1", 1),
            RetrievalStatus.OK,
        )


def _order_payload():
    return {
        "order_id": "order-1",
        "status": "shipped",
        "version": 3,
        "updated_at": NOW.isoformat(),
        "private_note": "only-owner-holds-this-value",
    }


def _adapter(adapter_id, adapter_version):
    return EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1()).adapter(
        adapter_id, adapter_version
    )


def _issue_order(payload=None):
    return _adapter(
        "business-tool-evidence-adapter",
        "business-tool-evidence-adapter-v1",
    ).issue(
        requirement_id="order.current_state",
        producer_id="order_lookup",
        producer_version="order-view-v1",
        locator=BusinessToolLocator(
            "call-1", "order_lookup", "order-1", "3"
        ),
        status=ToolCallStatus.SUCCESS,
        observed_at=NOW,
        payload=payload or _order_payload(),
    )


class Resolver:
    def __init__(self, payload):
        self.payload = payload

    def resolve(self, locator):
        assert locator is not None
        return self.payload


def test_receipt_keeps_only_locator_fields_and_hash_and_round_trips():
    policies = AuthorityPolicyRegistry.v1()
    issuer = EvidenceReceiptIssuer(policies)
    receipt = _issue_order()
    serialized = receipt.to_dict()
    restored = issuer.restore(serialized)

    assert restored == receipt
    assert "private_note" not in str(serialized)
    assert serialized["locator"]["object_ref"] == "order-1"
    assert len(serialized["content_sha256"]) == 64
    assert EvidenceReceiptVerifier().verify(
        restored, Resolver(_order_payload()), now=NOW
    ) is RequirementStatus.SATISFIED


def test_locator_can_be_dereferenced_to_detect_changed_owner_content_and_staleness():
    receipt = _issue_order()
    verifier = EvidenceReceiptVerifier()
    changed = {**_order_payload(), "status": "cancelled"}

    assert verifier.verify(
        receipt, Resolver(changed), now=NOW
    ) is RequirementStatus.INVALID_EVIDENCE
    assert verifier.verify(
        receipt, Resolver(_order_payload()), now=NOW + timedelta(seconds=61)
    ) is RequirementStatus.STALE


def test_action_receipt_binds_committed_effect_to_owner_receipt_id():
    payload = {
        "refund_id": "refund-1", "receipt_id": "refund-1",
        "order_id": "order-1", "status": "requested",
    }
    receipt = _adapter(
        "action-receipt-evidence-adapter", "action-receipt-evidence-adapter-v1"
    ).issue(
        requirement_id="refund.request_action",
        producer_id="refund_request_create",
        producer_version="refund-request-result-v1",
        locator=ActionReceiptLocator(
            "call-2", "refund_request_create", "refund-1"
        ),
        status=ToolEffectStatus.COMMITTED,
        observed_at=NOW,
        payload=payload,
    )
    assert EvidenceReceiptVerifier().verify(
        receipt, Resolver(payload), now=NOW
    ) is RequirementStatus.SATISFIED
    assert EvidenceReceiptVerifier().verify(
        receipt, Resolver({**payload, "receipt_id": "forged"}), now=NOW
    ) is RequirementStatus.INVALID_EVIDENCE

    unknown = _adapter(
        "action-receipt-evidence-adapter", "action-receipt-evidence-adapter-v1"
    ).issue(
        requirement_id="refund.request_action",
        producer_id="refund_request_create",
        producer_version="refund-request-result-v1",
        locator=ActionReceiptLocator(
            "call-2", "refund_request_create", "refund-1"
        ),
        status=ToolEffectStatus.OUTCOME_UNKNOWN,
        observed_at=NOW,
        payload=payload,
    )
    assert EvidenceReceiptVerifier().verify(
        unknown, Resolver(payload), now=NOW
    ) is RequirementStatus.MISSING


def test_knowledge_locator_binds_source_revision_and_checksum():
    payload = {
        "source_id": "source-1", "source_revision": "revision-1",
        "checksum": SHA, "content": "退款到账时间以支付渠道为准",
    }
    adapter = _adapter(
        "knowledge-evidence-adapter", "knowledge-evidence-adapter-v1"
    )
    receipt = adapter.issue(
        requirement_id="knowledge.active_source",
        producer_id="knowledge_search",
        producer_version="knowledge-candidates-v1",
        locator=KnowledgeLocator(
            "generation-1", "source-1", "revision-1", SHA, 0, 10
        ),
        status=RetrievalStatus.OK,
        observed_at=NOW,
        payload=payload,
    )
    assert EvidenceReceiptVerifier().verify(
        receipt, Resolver(payload), now=NOW
    ) is RequirementStatus.SATISFIED
    with pytest.raises(EvidenceContractError, match="locator does not bind"):
        adapter.issue(
            requirement_id="knowledge.active_source",
            producer_id="knowledge_search",
            producer_version="knowledge-candidates-v1",
            locator=KnowledgeLocator(
                "generation-1", "source-1", "wrong-revision", SHA, 0, 10
            ),
            status=RetrievalStatus.OK,
            observed_at=NOW,
            payload=payload,
        )


def test_only_registry_authorized_adapter_version_and_producer_can_issue():
    issuer = EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1())
    adapter = issuer.adapter(
        "business-tool-evidence-adapter",
        "business-tool-evidence-adapter-v1",
    )
    values = dict(
        requirement_id="order.current_state",
        producer_id="order_lookup",
        producer_version="order-view-v1",
        locator=BusinessToolLocator(
            "call-1", "order_lookup", "order-1", "3"
        ),
        status=ToolCallStatus.SUCCESS,
        observed_at=NOW,
        payload=_order_payload(),
    )
    for adapter_id, adapter_version in (
        ("invented-adapter", "business-tool-evidence-adapter-v1"),
        ("business-tool-evidence-adapter", "business-tool-evidence-adapter-v99"),
    ):
        with pytest.raises(AuthorityContractError):
            issuer.adapter(adapter_id, adapter_version)
    for changes in (
        {"producer_id": "knowledge_search"},
        {"producer_version": "order-view-v99"},
    ):
        with pytest.raises(AuthorityContractError):
            adapter.issue(**{**values, **changes})


def test_diagnostic_text_missing_provenance_and_unknown_wire_values_fail_closed():
    issuer = EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1())
    adapter = issuer.adapter(
        "business-tool-evidence-adapter",
        "business-tool-evidence-adapter-v1",
    )
    values = dict(
        requirement_id="order.current_state",
        producer_id="order_lookup",
        producer_version="order-view-v1",
        locator=BusinessToolLocator(
            "call-1", "order_lookup", "order-1", "3"
        ),
        status=ToolCallStatus.SUCCESS,
        observed_at=NOW,
    )
    with pytest.raises(EvidenceContractError, match="diagnostic text"):
        adapter.issue(**values, payload="订单已发货")
    with pytest.raises(EvidenceContractError, match="provenance"):
        BusinessToolLocator("call-1", "", "order-1", "3")

    serialized = _issue_order().to_dict()
    for key, unknown in (("kind", "LEGACY"), ("status", "public")):
        forged = {**serialized, key: unknown}
        with pytest.raises(EvidenceContractError):
            issuer.restore(forged)


def test_wire_checksum_and_policy_provenance_are_revalidated():
    issuer = EvidenceReceiptIssuer(AuthorityPolicyRegistry.v1())
    serialized = _issue_order().to_dict()
    for key, value in (
        ("authority", "knowledge.active_source"),
        ("policy_fingerprint", "b" * 64),
        ("receipt_sha256", "c" * 64),
    ):
        forged = {**serialized, key: value}
        with pytest.raises(EvidenceContractError):
            issuer.restore(forged)
