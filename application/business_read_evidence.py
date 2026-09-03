"""Convert captured business reads into governed evidence coverage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from application.authority_policy import AuthorityPolicyRegistry, FactRequirement
from application.coverage_gate import RequirementCoverageGate, RequirementCoverageReport
from application.evidence_receipt import (
    BusinessToolLocator,
    EvidenceReceipt,
    EvidenceReceiptIssuer,
    EvidenceResolver,
)
from mcp.tool_manager import ToolCallStatus, ToolResult


class BusinessReadEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessReadEvidence:
    receipts: tuple[EvidenceReceipt, ...]
    coverage: RequirementCoverageReport


class _CapturedResultResolver(EvidenceResolver):
    def __init__(self, payloads: Mapping[str, Mapping[str, Any]]) -> None:
        self._payloads = dict(payloads)

    def resolve(self, locator: Any) -> Mapping[str, Any]:
        if not isinstance(locator, BusinessToolLocator):
            raise BusinessReadEvidenceError("expected a business-tool locator")
        return self._payloads[locator.call_id]


def build_business_read_evidence(
    requirements: tuple[FactRequirement, ...],
    results: tuple[ToolResult, ...],
    *,
    arguments: Mapping[str, Any],
    observed_at: datetime,
) -> BusinessReadEvidence:
    policies = AuthorityPolicyRegistry.v1()
    adapter = EvidenceReceiptIssuer(policies).adapter(
        "business-tool-evidence-adapter",
        "business-tool-evidence-adapter-v1",
    )
    receipts = []
    payloads: dict[str, Mapping[str, Any]] = {}
    for requirement, result in zip(requirements, results, strict=False):
        if not result.success:
            break
        if not isinstance(result.data, Mapping):
            raise BusinessReadEvidenceError(
                "business producer returned unstructured output"
            )
        locator = _locator(result, arguments)
        payloads[result.call_id] = result.data
        receipts.append(
            adapter.issue(
                requirement_id=requirement.requirement_id,
                producer_id=result.tool_name,
                producer_version=result.output_schema_version,
                locator=locator,
                status=ToolCallStatus(result.status),
                observed_at=observed_at,
                payload=result.data,
            )
        )
    resolver = _CapturedResultResolver(payloads)
    coverage = RequirementCoverageGate(policies).evaluate(
        requirements,
        receipts,
        resolvers={
            tool: resolver
            for requirement in requirements
            for tool in requirement.allowed_tools
        },
        now=observed_at,
    )
    return BusinessReadEvidence(tuple(receipts), coverage)


def _locator(
    result: ToolResult,
    arguments: Mapping[str, Any],
) -> BusinessToolLocator:
    order_id = str(arguments.get("order_id") or "")
    if not order_id or str(result.data.get("order_id") or "") != order_id:
        raise BusinessReadEvidenceError(
            "business result is not bound to the requested order"
        )
    object_version = next(
        (
            str(result.data[key])
            for key in ("version", "order_version", "updated_at", "occurred_at")
            if result.data.get(key) is not None
        ),
        "",
    )
    if not object_version:
        raise BusinessReadEvidenceError("business result has no authoritative version")
    return BusinessToolLocator(
        result.call_id,
        result.tool_name,
        order_id,
        object_version,
    )
