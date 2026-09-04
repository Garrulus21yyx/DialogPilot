import asyncio
from datetime import datetime, timezone

import pytest

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    EvidenceRequest,
    FactRecord,
    FactSourceKind,
)
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import AgentContextView
from application.work_item import ArgumentValue, ControlMode, WorkItem
from infrastructure.target_evidence_resolution import (
    EvidenceResolutionError,
    TargetEvidenceResolver,
)


def _parent(registry):
    return WorkItem(
        "product-question-1",
        "product_technical",
        "Answer a product question",
        ControlMode.DELEGATED,
        ("media_read", "catalog_search", "knowledge_search"),
        ("product_identification", "product_qa"),
        (),
        ("knowledge.active_source",),
        (),
        CapabilityEffect.READ,
        CapabilityRisk.MEDIUM,
        "agent-result-v1",
        "customer-service-default:v1",
        3,
        registry.fingerprint,
        8,
        4,
    )


class _DirectEvidenceExecutor:
    def __init__(self):
        self.contexts = []

    async def __call__(self, context):
        self.contexts.append(context)
        item = context.work_item
        return AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "CATALOG_MATCH",
            "direct-evidence-test-v1",
            facts=(FactRecord(
                "product:SKU-9",
                "product.canonical_model",
                '{"model":"PX-200"}',
                FactSourceKind.VERIFIED_STATE,
                "catalog-receipt:SKU-9",
                "catalog_search",
                "catalog-v1",
                datetime.now(timezone.utc),
            ),),
        )


def test_registry_evidence_resolver_executes_only_requested_allowed_read_tool():
    registry = build_default_capability_registry("tenant-a")
    direct = _DirectEvidenceExecutor()
    resolver = TargetEvidenceResolver(registry, direct)
    parent = _parent(registry)
    context = AgentContextView(
        parent,
        "识别并回答商品问题",
        (),
        (),
        (),
        4000,
        {"tenant_id": "tenant-a", "user_id": "user-a"},
    )

    facts = asyncio.run(resolver(EvidenceRequest(
        "product.canonical_model",
        parent.work_item_id,
        ("catalog_search", "media_read"),
        (ArgumentValue.create("query", "SKU-9"),),
    ), context))

    assert facts[0].requirement_id == "product.canonical_model"
    evidence_item = direct.contexts[0].work_item
    assert evidence_item.control_mode is ControlMode.DIRECT
    assert evidence_item.allowed_tools == ("catalog_search",)
    assert dict(
        (value.name, value.value) for value in evidence_item.arguments
    ) == {"query": "SKU-9"}


def test_registry_evidence_resolver_rejects_provider_outside_work_item_envelope():
    registry = build_default_capability_registry("tenant-a")
    parent = _parent(registry)
    resolver = TargetEvidenceResolver(registry, _DirectEvidenceExecutor())
    context = AgentContextView(parent, "question", (), (), (), 4000, {})

    with pytest.raises(EvidenceResolutionError, match="WorkItem envelope"):
        asyncio.run(resolver(EvidenceRequest(
            "refund.current_state",
            parent.work_item_id,
            ("refund_status",),
            (ArgumentValue.create("order_id", "DP1234"),),
        ), context))
