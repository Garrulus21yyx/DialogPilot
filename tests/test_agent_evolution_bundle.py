import json

import pytest

from agents.agent_orchestrator import AgentOrchestrator, Request
from core.intent_recognizer import IntentCategory
from mcp.tool_manager import MCPToolManager, Tool, ToolRisk
from services.evolution import (
    AgentBundle,
    AgentBundleRegistry,
    BundleConflictError,
    BundleContractError,
    EvolutionEnvelope,
)


def _bundle(version="agent-v1", base=""):
    return AgentBundle(
        version=version,
        base_version=base,
        prompts={"billing": "引用账务事实后再给出结论。"},
        routing_policy={"supporting_threshold": 0.6, "clarification_threshold": 0.7},
        retrieval_policy={
            "top_k": 4,
            "rrf_k": 30,
            "vector_weight": 0.3,
            "lexical_weight": 0.7,
        },
        tool_descriptions={"lookup": "查询已认证用户自己的订单。"},
        model_policy={"provider": "deepseek", "roles": {"worker": {"model": "flash"}}},
        source_badcase_groups=("wrong-refund-route",),
    )


def test_bundle_is_canonical_and_nested_values_are_immutable():
    bundle = _bundle()
    restored = AgentBundle(**json.loads(json.dumps(bundle.to_dict(), ensure_ascii=False)))
    assert restored.content_hash == bundle.content_hash
    with pytest.raises(TypeError):
        bundle.routing_policy["supporting_threshold"] = 0.1


def test_bundle_rejects_security_owned_surface():
    with pytest.raises(BundleContractError, match="security-owned"):
        AgentBundle(version="unsafe-v1", routing_policy={"approval_mode": "auto"})


def test_registry_is_append_only_and_pointer_is_separate(tmp_path):
    registry = AgentBundleRegistry(str(tmp_path / "bundles.db"))
    v1 = registry.bootstrap(_bundle())
    pinned = registry.active()
    v2 = registry.register(_bundle("agent-v2", "agent-v1"), actor="evolution")
    assert registry.active().version == v1.version
    assert v2.version == "agent-v2"
    assert registry.active().version == "agent-v1"
    assert pinned.version == "agent-v1"

    with pytest.raises(BundleConflictError):
        registry.register(AgentBundle(version="agent-v2", base_version="agent-v1"))


def test_bootstrap_successor_only_migrates_exact_legacy_retrieval_policy(tmp_path):
    legacy_policy = {
        "top_k": 3, "rrf_k": 60, "vector_weight": 0.0, "lexical_weight": 1.0,
    }
    registry = AgentBundleRegistry(str(tmp_path / "bundles.db"))
    registry.bootstrap(AgentBundle(version="agent-v1", retrieval_policy=legacy_policy))
    successor = AgentBundle(
        version="agent-v2-rag", base_version="agent-v1",
        retrieval_policy={
            "top_k": 5, "candidate_k": 20, "context_max_tokens": 2600,
            "rrf_k": 10, "vector_weight": 0.25, "lexical_weight": 0.75,
            "raw_query_weight": 0.25, "standalone_query_weight": 0.75,
        },
    )

    _, migrated = registry.bootstrap_successor(
        successor,
        predecessor_version="agent-v1",
        expected_predecessor_retrieval=legacy_policy,
    )

    assert migrated is True
    assert registry.active().version == "agent-v2-rag"


def test_bootstrap_successor_preserves_custom_active_pointer(tmp_path):
    registry = AgentBundleRegistry(str(tmp_path / "bundles.db"))
    registry.bootstrap(_bundle())
    successor = AgentBundle(version="agent-v2-rag", base_version="agent-v1")

    _, migrated = registry.bootstrap_successor(
        successor,
        predecessor_version="agent-v1",
        expected_predecessor_retrieval={"top_k": 3},
    )

    assert migrated is False
    assert registry.active().version == "agent-v1"


def test_envelope_contains_hashes_not_raw_prompt_or_output():
    envelope = EvolutionEnvelope.from_execution(
        request_id="req-1",
        trace_id="trace-1",
        bundle=_bundle(),
        tool_registry_hash="a" * 64,
        producer_agent_keys=["billing_0"],
        task_ids=["billing_task"],
        tool_call_ids=["call-1"],
        verification="reject",
        badcase_group="wrong-refund-route",
    ).to_dict()
    serialized = json.dumps(envelope, ensure_ascii=False)
    assert envelope["agent_bundle_version"] == "agent-v1"
    assert "引用账务事实" not in serialized
    assert set(envelope) >= {"prompt_hash", "router_policy_hash", "tool_registry_hash"}


def test_routing_threshold_comes_from_pinned_bundle():
    req = Request(
        message="退款并登录失败",
        user_id="u1",
        conv_id="c1",
        intent=IntentCategory.BILLING,
        agent_bundle=_bundle(),
        bundle_version="agent-v1",
    )
    assert AgentOrchestrator._bundle_number(
        req, "routing_policy", "supporting_threshold", 0.45,
    ) == 0.6


def test_tool_fingerprint_tracks_description_but_not_relaxes_permissions():
    manager = MCPToolManager(api_key="test", model="test")
    manager.register(Tool(
        name="lookup",
        description="旧描述",
        handler=lambda params, context: {},
        schema={"type": "object", "properties": {}},
        allowed_agents=("billing",),
        risk=ToolRisk.HIGH,
        read_only=False,
        requires_approval=True,
    ))
    base_hash = manager.registry_fingerprint()
    changed_hash = manager.registry_fingerprint({"lookup": "新描述"})
    projected = manager.anthropic_tools_for_agent(
        "billing", description_overrides={"lookup": "新描述"},
    )
    assert changed_hash != base_hash
    assert projected[0]["description"] == "新描述"
    assert manager.tools_for_agent("general") == []
    assert manager.tools_for_agent("billing")[0].requires_approval is True
