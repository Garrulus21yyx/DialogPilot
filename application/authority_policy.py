"""Versioned minimum fact requirements and governed tool manifest validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from application.route_decision import (
    RequiredAuthority,
    RouteDecision,
    RouteMode,
)


class AuthorityContractError(ValueError):
    pass


class UnsupportedAuthority(AuthorityContractError):
    pass


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


class AuthorityPolicyRegistry:
    version = "authority-policy-registry-v1"

    def __init__(self, requirements: Sequence[FactRequirement]):
        self._requirements = {item.requirement_id: item for item in requirements}
        if len(self._requirements) != len(requirements):
            raise AuthorityContractError("duplicate requirement ID")

    @classmethod
    def v1(cls) -> "AuthorityPolicyRegistry":
        read = RequirementEffect.READ
        write = RequirementEffect.WRITE
        supported = AuthoritySupport.SUPPORTED
        unsupported = AuthoritySupport.UNSUPPORTED
        return cls((
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
                "memory.prior_event", "memory.prior_event",
                ("memory_id", "conversation_id", "event_seq"), 604800,
                read, ("memory_search",), (), "", supported,
                "Memory:event-v1",
            ),
            FactRequirement(
                "commitment.current_state", "commitment.current_state",
                (), 60, read, (), ("knowledge_search",), "", unsupported,
                "Commitment:pending-m4",
            ),
        ))

    @property
    def fingerprint(self) -> str:
        payload = [
            {
                **item.__dict__,
                "effect": item.effect.value,
                "support": item.support.value,
            }
            for item in sorted(self._requirements.values(), key=lambda row: row.requirement_id)
        ]
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
                "unsupported authority: " + ",".join(unsupported_items)
            )
        return requirements

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
        missing = tuple(
            field for field in requirement.required_fields
            if field not in output or output[field] is None
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
        raise UnsupportedAuthority("no supported action authority for intent")
