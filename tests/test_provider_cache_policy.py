"""M4-T03 native prompt-cache capability/privacy conformance."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.llm_metrics import capture_llm_usage, create_message
from core.model_policy import ModelProfile, ModelRole
from core.provider_cache_policy import (
    CacheDataClass,
    CacheDeletionHandling,
    PromptCacheCapability,
    PromptCacheMode,
    ProviderCacheGate,
    ProviderCacheInvocation,
    ProviderCachePolicy,
    ProviderCacheRequest,
    ProviderCacheStatus,
)


def _invocation():
    capability = PromptCacheCapability(
        provider="anthropic", mode=PromptCacheMode.EXPLICIT,
        ttl_seconds=(300, 3600), multimodal=False,
        usage_fields=("cache_creation_input_tokens", "cache_read_input_tokens"),
        no_training=True, zero_retention=False,
        deletion_handling=(CacheDeletionHandling.TTL_ONLY,),
    )
    policy = ProviderCachePolicy(
        tenant_id="tenant-a", regions=frozenset({"eu"}),
        allowed_data_classes=frozenset({
            CacheDataClass.SYSTEM, CacheDataClass.PROFILE,
            CacheDataClass.TOOL_SCHEMA,
        }),
        max_ttl_seconds=300, require_no_training=True,
        require_zero_retention=False,
        deletion_handling=CacheDeletionHandling.TTL_ONLY,
    )
    request = ProviderCacheRequest(
        tenant_id="tenant-a", region="eu",
        data_classes=frozenset({
            CacheDataClass.SYSTEM, CacheDataClass.TOOL_SCHEMA,
        }),
        ttl_seconds=300,
    )
    return ProviderCacheInvocation(
        capability, policy, request,
        stable_system_prefix="stable system", cache_tool_schemas=True,
    )


def test_explicit_cache_marks_only_stable_prefix_and_tool_schema_breakpoint():
    payload, decision = ProviderCacheGate().apply({
        "system": "stable system\nlive service continuity",
        "messages": [{"role": "user", "content": "current input"}],
        "tools": [{"name": "one"}, {"name": "two"}],
    }, _invocation())
    assert decision.status is ProviderCacheStatus.ENABLED
    assert payload["system"][0]["cache_control"] == {
        "type": "ephemeral", "ttl": "5m",
    }
    assert "cache_control" not in payload["system"][1]
    assert "cache_control" not in payload["tools"][0]
    assert payload["tools"][1]["cache_control"]["ttl"] == "5m"
    assert payload["messages"] == [
        {"role": "user", "content": "current input"},
    ]


@pytest.mark.parametrize(("mutation", "reason"), [
    (lambda item: replace(item, request=replace(item.request, tenant_id="other")), "tenant_mismatch"),
    (lambda item: replace(item, request=replace(item.request, region="us")), "region_mismatch"),
    (lambda item: replace(item, request=replace(
        item.request, data_classes=frozenset({CacheDataClass.CURRENT_INPUT}),
    )), "data_class_denied"),
    (lambda item: replace(item, request=replace(item.request, ttl_seconds=3600)), "policy_ttl_exceeded"),
    (lambda item: replace(item, request=replace(item.request, multimodal=True)), "multimodal_unsupported"),
    (lambda item: replace(item, capability=replace(item.capability, no_training=False)), "no_training_contract_missing"),
    (lambda item: replace(
        item, policy=replace(item.policy, require_zero_retention=True),
    ), "zero_retention_contract_missing"),
    (lambda item: replace(
        item, policy=replace(
            item.policy, deletion_handling=CacheDeletionHandling.PROVIDER_DELETE,
        ),
    ), "deletion_handling_mismatch"),
    (lambda item: replace(
        item, capability=replace(item.capability, usage_fields=()),
    ), "cache_usage_fields_missing"),
])
def test_any_capability_privacy_or_retention_mismatch_disables_cache(
    mutation, reason,
):
    decision = ProviderCacheGate().evaluate(mutation(_invocation()))
    assert (decision.status, decision.reason) == (ProviderCacheStatus.DISABLED, reason)


def test_provider_boundary_records_policy_and_provider_cache_usage_without_semantic_change():
    class Messages:
        payload = None

        async def create(self, **payload):
            self.payload = payload
            return SimpleNamespace(
                id="cached", content=[], usage=SimpleNamespace(
                    input_tokens=20, output_tokens=3,
                    cache_creation_input_tokens=10,
                    cache_read_input_tokens=4,
                ),
            )

    messages = Messages()
    with capture_llm_usage() as collector:
        asyncio.run(create_message(
            SimpleNamespace(messages=messages), ModelProfile("test-model"),
            ModelRole.WORKER, max_tokens=64,
            system="stable system\nlive suffix",
            messages=[{"role": "user", "content": "current input"}],
            provider_cache=_invocation(),
        ))
    call = collector.calls[0]
    assert call.cache_policy_status == "ENABLED"
    assert call.cache_creation_input_tokens == 10
    assert call.cache_read_input_tokens == 4
    assert messages.payload["messages"][0]["content"] == "current input"


def test_invalid_cache_invocation_type_fails_before_provider_call():
    with pytest.raises(TypeError, match="ProviderCacheInvocation"):
        asyncio.run(create_message(
            SimpleNamespace(messages=SimpleNamespace()), ModelProfile("test-model"),
            ModelRole.WORKER, max_tokens=64, messages=[], provider_cache={},
        ))


def test_cache_policy_has_closed_runtime_modes_and_data_classes():
    assert set(PromptCacheMode)
    assert set(CacheDataClass)
