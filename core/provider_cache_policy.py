"""Native provider prompt-cache capability and privacy-policy gate."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping


class PromptCacheMode(str, Enum):
    DISABLED = "disabled"
    IMPLICIT = "implicit"
    EXPLICIT = "explicit"


class CacheDataClass(str, Enum):
    SYSTEM = "system"
    PROFILE = "profile"
    TOOL_SCHEMA = "tool_schema"
    FEW_SHOT = "few_shot"
    CURRENT_INPUT = "current_input"
    SERVICE_CONTINUITY = "service_continuity"
    TOOL_RESULT = "tool_result"
    MULTIMODAL = "multimodal"


class CacheDeletionHandling(str, Enum):
    TTL_ONLY = "ttl_only"
    PROVIDER_DELETE = "provider_delete"


class ProviderCacheStatus(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class PromptCacheCapability:
    provider: str
    mode: PromptCacheMode
    ttl_seconds: tuple[int, ...]
    multimodal: bool
    usage_fields: tuple[str, ...]
    no_training: bool
    zero_retention: bool
    deletion_handling: tuple[CacheDeletionHandling, ...]


@dataclass(frozen=True)
class ProviderCachePolicy:
    tenant_id: str
    regions: frozenset[str]
    allowed_data_classes: frozenset[CacheDataClass]
    max_ttl_seconds: int
    require_no_training: bool
    require_zero_retention: bool
    deletion_handling: CacheDeletionHandling
    version: str = "provider-cache-policy-v1"


@dataclass(frozen=True)
class ProviderCacheRequest:
    tenant_id: str
    region: str
    data_classes: frozenset[CacheDataClass]
    ttl_seconds: int
    multimodal: bool = False


@dataclass(frozen=True)
class ProviderCacheDecision:
    status: ProviderCacheStatus
    reason: str
    mode: PromptCacheMode = PromptCacheMode.DISABLED
    ttl_seconds: int = 0


@dataclass(frozen=True)
class ProviderCacheInvocation:
    capability: PromptCacheCapability
    policy: ProviderCachePolicy
    request: ProviderCacheRequest
    stable_system_prefix: str = ""
    cache_tool_schemas: bool = False


class ProviderCacheGate:
    def evaluate(self, invocation: ProviderCacheInvocation) -> ProviderCacheDecision:
        capability, policy, request = (
            invocation.capability, invocation.policy, invocation.request,
        )
        checks = (
            (capability.mode is not PromptCacheMode.DISABLED, "provider_unsupported"),
            (request.tenant_id == policy.tenant_id, "tenant_mismatch"),
            (request.region in policy.regions, "region_mismatch"),
            (request.data_classes <= policy.allowed_data_classes, "data_class_denied"),
            (request.ttl_seconds <= policy.max_ttl_seconds, "policy_ttl_exceeded"),
            (request.ttl_seconds in capability.ttl_seconds, "provider_ttl_unsupported"),
            (not request.multimodal or capability.multimodal, "multimodal_unsupported"),
            (
                not policy.require_no_training or capability.no_training,
                "no_training_contract_missing",
            ),
            (
                not policy.require_zero_retention or capability.zero_retention,
                "zero_retention_contract_missing",
            ),
            (
                policy.deletion_handling in capability.deletion_handling,
                "deletion_handling_mismatch",
            ),
            (
                {"cache_creation_input_tokens", "cache_read_input_tokens"}
                <= set(capability.usage_fields),
                "cache_usage_fields_missing",
            ),
        )
        for passed, reason in checks:
            if not passed:
                return ProviderCacheDecision(ProviderCacheStatus.DISABLED, reason)
        return ProviderCacheDecision(
            ProviderCacheStatus.ENABLED, "capability_and_privacy_policy_match",
            capability.mode, request.ttl_seconds,
        )

    def apply(
        self,
        payload: Mapping[str, Any],
        invocation: ProviderCacheInvocation,
    ) -> tuple[dict[str, Any], ProviderCacheDecision]:
        decision = self.evaluate(invocation)
        request = dict(payload)
        if decision.status is ProviderCacheStatus.DISABLED:
            return request, decision
        if decision.mode is PromptCacheMode.IMPLICIT:
            return request, decision
        prefix = invocation.stable_system_prefix
        system = request.get("system")
        if not prefix or not isinstance(system, str) or not system.startswith(prefix):
            return request, replace(
                decision, status=ProviderCacheStatus.DISABLED,
                reason="stable_prefix_mismatch", mode=PromptCacheMode.DISABLED,
                ttl_seconds=0,
            )
        ttl = {300: "5m", 3600: "1h"}.get(decision.ttl_seconds)
        if ttl is None:
            return request, replace(
                decision, status=ProviderCacheStatus.DISABLED,
                reason="explicit_ttl_encoding_unsupported",
                mode=PromptCacheMode.DISABLED, ttl_seconds=0,
            )
        blocks: list[dict[str, Any]] = [{
            "type": "text", "text": prefix,
            "cache_control": {"type": "ephemeral", "ttl": ttl},
        }]
        suffix = system[len(prefix):]
        if suffix:
            blocks.append({"type": "text", "text": suffix})
        request["system"] = blocks
        if invocation.cache_tool_schemas:
            tools = [dict(item) for item in request.get("tools") or ()]
            if tools:
                tools[-1]["cache_control"] = {"type": "ephemeral", "ttl": ttl}
                request["tools"] = tools
        return request, decision


DEFAULT_PROVIDER_CACHE_GATE = ProviderCacheGate()
