"""Versioned minimum fact requirements and governed tool manifest validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from application.route_decision import (
    RequiredAuthority,
    RouteDecision,
    RouteMode,
    RouteRisk,
)


class AuthorityContractError(ValueError):
    pass


class UnsupportedAuthority(AuthorityContractError):
    def __init__(
        self,
        message: str,
        *,
        requirement_ids: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.requirement_ids = requirement_ids


class RequirementEffect(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


class AuthoritySupport(str, Enum):
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class FactRequirement:
    requirement_id: str
    authority: str
    required_fields: tuple[str, ...]
    freshness_seconds: int | None
    effect: RequirementEffect
    allowed_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    receipt_schema_version: str
    support: AuthoritySupport
    owner_approval: str
    version: str = "fact-requirement-v1"

    def __post_init__(self) -> None:
        if not self.requirement_id or not self.authority or not self.owner_approval:
            raise AuthorityContractError("requirement identity/owner is required")
        if self.support is AuthoritySupport.SUPPORTED and not self.allowed_tools:
            raise AuthorityContractError("supported authority requires an allowed tool")
        if self.effect is RequirementEffect.WRITE and not self.receipt_schema_version:
            raise AuthorityContractError("write requirement requires receipt schema")
        if set(self.allowed_tools).intersection(self.forbidden_tools):
            raise AuthorityContractError("tool cannot be both allowed and forbidden")

    @property
    def claim_type(self) -> str:
        """Stable claim identifier used by planners and coverage consumers."""
        return self.requirement_id

    @property
    def required_tool(self) -> str | None:
        """Project the sole required producer when the authority has exactly one."""
        return self.allowed_tools[0] if len(self.allowed_tools) == 1 else None

    @property
    def required_output_fields(self) -> tuple[str, ...]:
        return self.required_fields

    @property
    def required_effect(self) -> str:
        return self.effect.value


@dataclass(frozen=True)
class RequirementOutputCheck:
    requirement_id: str
    satisfied: bool
    missing_fields: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True)
class EvidenceAdapterRegistration:
    adapter_id: str
    adapter_version: str
    evidence_kind: str
    requirement_ids: tuple[str, ...]
    producer_versions: tuple[tuple[str, str], ...]
    receipt_schema_version: str = "evidence-receipt-v1"

    def __post_init__(self) -> None:
        if any(not value for value in (
            self.adapter_id, self.adapter_version, self.evidence_kind,
            self.receipt_schema_version,
        )):
            raise AuthorityContractError("evidence adapter identity is required")
        if not self.requirement_ids or not self.producer_versions:
            raise AuthorityContractError("evidence adapter scope is required")
        producer_ids = [item[0] for item in self.producer_versions]
        if len(set(producer_ids)) != len(producer_ids) or any(
            not producer_id or not version
            for producer_id, version in self.producer_versions
        ):
            raise AuthorityContractError("evidence producer versions are invalid")

    @property
    def producer_ids(self) -> tuple[str, ...]:
        return tuple(item[0] for item in self.producer_versions)


class AuthorityPolicyRegistry:
    version = "authority-policy-registry-v1"
    route_resolution_version = "authority-route-resolution-v1"

    def __init__(
        self,
        requirements: Sequence[FactRequirement],
        evidence_adapters: Sequence[EvidenceAdapterRegistration] = (),
    ):
        self._requirements = {item.requirement_id: item for item in requirements}
        if len(self._requirements) != len(requirements):
            raise AuthorityContractError("duplicate requirement ID")
        self._evidence_adapters = {
            item.adapter_id: item for item in evidence_adapters
        }
        if len(self._evidence_adapters) != len(evidence_adapters):
            raise AuthorityContractError("duplicate evidence adapter ID")
        for adapter in evidence_adapters:
            for requirement_id in adapter.requirement_ids:
                self.get(requirement_id)

    @classmethod
    def v1(cls) -> "AuthorityPolicyRegistry":
        read = RequirementEffect.READ
        write = RequirementEffect.WRITE
        supported = AuthoritySupport.SUPPORTED
        unsupported = AuthoritySupport.UNSUPPORTED
        requirements = (
            FactRequirement(
                "knowledge.active_source", "knowledge.active_source",
                ("source_id", "source_revision", "checksum", "content"), 86400,
                read, ("knowledge_search",), (), "", supported,
                "Knowledge:source-contract-v0",
            ),
            FactRequirement(
                "order.current_state", "order.current_state",
                ("order_id", "status", "version", "updated_at"), 60,
                read, ("order_lookup",), ("knowledge_search",), "", supported,
                "CustomerOperations:order-v1",
            ),
            FactRequirement(
                "refund.current_state", "refund.current_state",
                ("refund_id", "order_id", "status", "updated_at"), 60,
                read, ("refund_status",), ("knowledge_search",), "", supported,
                "CustomerOperations:refund-v1",
            ),
            FactRequirement(
                "refund.eligibility", "refund.eligibility",
                ("order_id", "eligible", "reason_code", "order_version"), 60,
                read, ("refund_eligibility_check",), ("knowledge_search",), "",
                supported, "CustomerOperations:refund-eligibility-v1",
            ),
            FactRequirement(
                "refund.request_action", "refund.request_action",
                ("refund_id", "order_id", "status"), None,
                write, ("refund_request_create",), (), "action-receipt-v1",
                supported, "CustomerOperations:refund-action-v1",
            ),
            FactRequirement(
                "account.security_events", "account.security_events",
                ("event_id", "event_type", "severity", "occurred_at"), 60,
                read, ("account_security_event_list",), ("knowledge_search",), "",
                supported, "AccountSecurity:events-v1",
            ),
            FactRequirement(
                "account.current_state", "account.current_state",
                (), 60, read, (), ("knowledge_search",), "", unsupported,
                "Account:unsupported-v1",
            ),
            FactRequirement(
                "support.ticket_state", "support.ticket_state",
                ("ticket_id", "status", "priority", "updated_at"), 60,
                read, ("support_ticket_list", "support_ticket_get"), (), "",
                supported, "Ticket:state-v1",
            ),
            FactRequirement(
                "support.handoff_action", "support.handoff_action",
                ("ticket_id", "status"), None, write,
                ("support_ticket_create",), (), "action-receipt-v1", supported,
                "Ticket:handoff-v1",
            ),
            FactRequirement(
                "memory.service_episode", "memory.service_episode",
                (
                    "tenant_id", "user_id", "backend_id", "generation_id",
                    "episode_id", "episode_revision", "outcome_receipt_ref",
                    "provenance_sha256", "verified_at", "user_evidence_refs",
                    "assistant_evidence_refs",
                ), 604800, read, ("service_episode_search",), (), "", supported,
                "Memory:service-episode-v1",
            ),
            FactRequirement(
                "commitment.current_state", "commitment.current_state",
                (), 60, read, (), ("knowledge_search",), "", unsupported,
                "Commitment:pending-m4",
            ),
        )
        adapters = (
            EvidenceAdapterRegistration(
                "knowledge-evidence-adapter", "knowledge-evidence-adapter-v1",
                "KNOWLEDGE", ("knowledge.active_source",),
                (("knowledge_search", "knowledge-evidence-pack-result-v1"),),
            ),
            EvidenceAdapterRegistration(
                "business-tool-evidence-adapter", "business-tool-evidence-adapter-v1",
                "BUSINESS_TOOL", (
                    "order.current_state", "refund.current_state",
                    "refund.eligibility", "account.security_events",
                    "support.ticket_state",
                ), (
                    ("order_lookup", "order-view-v1"),
                    ("refund_status", "refund-view-v1"),
                    ("refund_eligibility_check", "refund-eligibility-v1"),
                    ("account_security_event_list", "security-events-v1"),
                    ("support_ticket_list", "ticket-list-v1"),
                    ("support_ticket_get", "ticket-view-v1"),
                ),
            ),
            EvidenceAdapterRegistration(
                "action-receipt-evidence-adapter", "action-receipt-evidence-adapter-v1",
                "ACTION_RECEIPT", (
                    "refund.request_action", "support.handoff_action",
                ), (
                    ("refund_request_create", "refund-request-result-v1"),
                    ("support_ticket_create", "ticket-create-result-v1"),
                ),
            ),
            EvidenceAdapterRegistration(
                "service-episode-evidence-adapter",
                "service-episode-evidence-adapter-v1",
                "SERVICE_EPISODE", ("memory.service_episode",),
                (("service_episode_search", "service-episode-hit-v1"),),
            ),
        )
        return cls(requirements, adapters)

    @property
    def fingerprint(self) -> str:
        payload = {
            "requirements": [{
                **item.__dict__,
                "effect": item.effect.value,
                "support": item.support.value,
            }
            for item in sorted(self._requirements.values(), key=lambda row: row.requirement_id)
            ],
            "evidence_adapters": [item.__dict__ for item in sorted(
                self._evidence_adapters.values(), key=lambda row: row.adapter_id
            )],
        }
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def get(self, requirement_id: str) -> FactRequirement:
        try:
            return self._requirements[requirement_id]
        except KeyError as exc:
            raise AuthorityContractError(
                f"unknown fact requirement: {requirement_id}"
            ) from exc

    def authorize_evidence_adapter(
        self,
        *,
        requirement_id: str,
        adapter_id: str,
        adapter_version: str,
        producer_id: str,
        producer_version: str,
    ) -> EvidenceAdapterRegistration:
        """Authorize the sole versioned conversion boundary for producer output."""
        requirement = self.get(requirement_id)
        adapter = self.get_evidence_adapter(adapter_id, adapter_version)
        if requirement_id not in adapter.requirement_ids:
            raise AuthorityContractError("adapter cannot issue this requirement")
        if producer_id not in adapter.producer_ids:
            raise AuthorityContractError("producer is not registered for adapter")
        if (producer_id, producer_version) not in adapter.producer_versions:
            raise AuthorityContractError("producer version is not registered for adapter")
        if producer_id not in requirement.allowed_tools:
            raise AuthorityContractError("producer is not allowed for requirement")
        return adapter

    def get_evidence_adapter(
        self,
        adapter_id: str,
        adapter_version: str,
    ) -> EvidenceAdapterRegistration:
        try:
            adapter = self._evidence_adapters[adapter_id]
        except KeyError as exc:
            raise AuthorityContractError(
                f"unregistered evidence adapter: {adapter_id}"
            ) from exc
        if adapter.adapter_version != adapter_version:
            raise AuthorityContractError("evidence adapter version mismatch")
        return adapter

    def minimum_requirements(self, route: RouteDecision) -> tuple[FactRequirement, ...]:
        ids: list[str] = []
        for authority in route.required_authorities:
            if authority is RequiredAuthority.KNOWLEDGE:
                ids.append("knowledge.active_source")
            elif authority is RequiredAuthority.ORDER_STATE:
                ids.append("order.current_state")
            elif authority is RequiredAuthority.REFUND_STATE:
                ids.append("refund.current_state")
            elif authority is RequiredAuthority.ACCOUNT_STATE:
                ids.append("account.current_state")
            elif authority is RequiredAuthority.SECURITY:
                ids.append("account.security_events")
            elif authority is RequiredAuthority.HUMAN:
                if route.mode is not RouteMode.HANDOFF:
                    ids.append("support.handoff_action")
            elif authority is RequiredAuthority.ACTION_APPROVAL:
                ids.append(self._action_requirement(route.intent))
            elif authority is RequiredAuthority.DOMAIN_TOOL:
                ids.append(self._domain_requirement(route.intent))
        requirements = tuple(self.get(item) for item in dict.fromkeys(ids))
        if route.mode not in {
            RouteMode.DIRECT, RouteMode.OUT_OF_SCOPE,
            RouteMode.CLARIFY, RouteMode.HANDOFF,
        } and not requirements:
            raise AuthorityContractError("non-terminal route has no minimum requirements")
        unsupported_items = [
            item.requirement_id for item in requirements
            if item.support is AuthoritySupport.UNSUPPORTED
        ]
        if unsupported_items:
            raise UnsupportedAuthority(
                "unsupported authority: " + ",".join(unsupported_items),
                requirement_ids=tuple(unsupported_items),
            )
        return requirements

    def resolve_route_authority(self, route: RouteDecision) -> RouteDecision:
        """Fail closed to canonical Handoff when the required authority has no owner."""
        try:
            self.minimum_requirements(route)
        except UnsupportedAuthority as exc:
            unsupported = exc.requirement_ids or ("unmapped.authority",)
            reason_codes = tuple(dict.fromkeys((
                *route.reason_codes,
                "AUTHORITY_UNSUPPORTED",
                *(f"UNSUPPORTED_REQUIREMENT:{item}" for item in unsupported),
            )))
            auxiliary = tuple(dict.fromkeys((
                *route.auxiliary_signals, "authority_fail_closed",
            )))
            return replace(
                route,
                mode=RouteMode.HANDOFF,
                required_authorities=(RequiredAuthority.HUMAN,),
                risk=RouteRisk.HIGH if route.risk is not RouteRisk.CRITICAL else route.risk,
                reason_codes=reason_codes,
                owner_ids=(),
                policy_version=(
                    f"{route.policy_version}|{self.route_resolution_version}"
                ),
                auxiliary_signals=auxiliary,
            )
        return route

    def resolve_requirements(
        self,
        route: RouteDecision,
        proposed_requirement_ids: Iterable[str],
    ) -> tuple[FactRequirement, ...]:
        minimum = self.minimum_requirements(route)
        result = {item.requirement_id: item for item in minimum}
        for requirement_id in proposed_requirement_ids:
            item = self.get(str(requirement_id))
            if item.support is AuthoritySupport.UNSUPPORTED:
                raise UnsupportedAuthority(item.requirement_id)
            result.setdefault(item.requirement_id, item)
        return tuple(result[key] for key in sorted(result))

    def validate_tool_manifest(self, tool: Any) -> None:
        fields = (
            "authority", "manifest_version", "output_schema_version",
            "preconditions", "idempotency", "retry_policy",
            "typed_outcomes", "output_fields",
        )
        if any(not getattr(tool, field, None) for field in fields):
            raise AuthorityContractError(
                f"tool manifest is incomplete: {getattr(tool, 'name', 'unknown')}"
            )
        if tool.manifest_version != "tool-manifest-v1":
            raise AuthorityContractError(
                f"unsupported tool manifest version: {tool.manifest_version}"
            )
        if not isinstance(tool.timeout_s, (int, float)) or tool.timeout_s <= 0:
            raise AuthorityContractError("tool timeout must be positive")
        matching = [
            item for item in self._requirements.values()
            if tool.name in item.allowed_tools
        ]
        if not matching or any(item.authority != tool.authority for item in matching):
            raise AuthorityContractError(
                f"tool authority is not registered: {tool.name}"
            )
        required_output_fields = {
            field for item in matching for field in item.required_fields
        }
        if (
            tool.output_schema_version == "knowledge-evidence-pack-result-v1"
            and "evidence_pack" in tool.output_fields
        ):
            required_output_fields = set()
        missing_manifest_fields = required_output_fields.difference(tool.output_fields)
        if missing_manifest_fields:
            raise AuthorityContractError(
                f"tool output manifest omits required fields: {tool.name}:"
                f"{','.join(sorted(missing_manifest_fields))}"
            )
        if tool.read_only:
            if tool.idempotency != "read_only" or tool.requires_approval:
                raise AuthorityContractError("read tool manifest effect is inconsistent")
        else:
            if not tool.requires_approval or not tool.receipt_schema_version:
                raise AuthorityContractError("write tool requires approval and receipt")
            if any(
                item.receipt_schema_version != tool.receipt_schema_version
                for item in matching
            ):
                raise AuthorityContractError("tool receipt schema mismatch")

    def validate_tools(self, tools: Iterable[Any]) -> None:
        for tool in tools:
            self.validate_tool_manifest(tool)

    def validate_output(
        self,
        requirement_id: str,
        *,
        tool: Any,
        output: Any,
        observed_at: datetime | None = None,
        now: datetime | None = None,
    ) -> RequirementOutputCheck:
        requirement = self.get(requirement_id)
        self.validate_tool_manifest(tool)
        if tool.name not in requirement.allowed_tools:
            return RequirementOutputCheck(
                requirement_id, False, requirement.required_fields,
                "TOOL_NOT_ALLOWED_FOR_REQUIREMENT",
            )
        if not isinstance(output, Mapping):
            return RequirementOutputCheck(
                requirement_id, False, requirement.required_fields,
                "STRUCTURED_TOOL_OUTPUT_REQUIRED",
            )
        normalized_output = output
        if tool.output_schema_version == "knowledge-evidence-pack-result-v1":
            normalized_output = self._knowledge_pack_output(output)
            if normalized_output is None:
                return RequirementOutputCheck(
                    requirement_id, False, requirement.required_fields,
                    "KNOWLEDGE_EVIDENCE_PACK_INVALID",
                )
        missing = tuple(
            field for field in requirement.required_fields
            if field not in normalized_output or normalized_output[field] is None
        )
        if missing:
            return RequirementOutputCheck(
                requirement_id, False, missing, "REQUIRED_FIELDS_MISSING",
            )
        if requirement.freshness_seconds is not None:
            if observed_at is None:
                return RequirementOutputCheck(
                    requirement_id, False, (), "OBSERVED_AT_REQUIRED",
                )
            current = now or datetime.now(timezone.utc)
            if observed_at.tzinfo is None or current.tzinfo is None:
                return RequirementOutputCheck(
                    requirement_id, False, (), "OBSERVED_AT_MUST_BE_TIMEZONE_AWARE",
                )
            age_seconds = (current - observed_at).total_seconds()
            if age_seconds < 0 or age_seconds > requirement.freshness_seconds:
                return RequirementOutputCheck(
                    requirement_id, False, (), "OBSERVATION_STALE",
                )
        return RequirementOutputCheck(
            requirement_id, True, (), "REQUIREMENT_SATISFIED",
        )

    @staticmethod
    def _knowledge_pack_output(output: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if output.get("status") != "OK":
            return None
        pack = output.get("evidence_pack")
        if not isinstance(pack, Mapping):
            return None
        items = pack.get("items")
        if not isinstance(items, list) or not items:
            return None
        normalized = []
        for item in items:
            if not isinstance(item, Mapping):
                return None
            source = item.get("source_ref")
            if not isinstance(source, Mapping):
                return None
            row = {
                "source_id": source.get("source_id"),
                "source_revision": source.get("source_revision"),
                "checksum": source.get("checksum"),
                "content": item.get("text"),
            }
            if any(value is None or value == "" for value in row.values()):
                return None
            normalized.append(row)
        return normalized[0]

    @staticmethod
    def _domain_requirement(intent: str) -> str:
        normalized = intent.lower()
        if "refund" in normalized:
            return "refund.current_state"
        if "security" in normalized:
            return "account.security_events"
        if "account" in normalized:
            return "account.current_state"
        return "order.current_state"

    @staticmethod
    def _action_requirement(intent: str) -> str:
        if "refund" in intent.lower():
            return "refund.request_action"
        requirement_id = f"action:{intent.strip().lower() or 'unknown'}"
        raise UnsupportedAuthority(
            "no supported action authority for intent",
            requirement_ids=(requirement_id,),
        )
