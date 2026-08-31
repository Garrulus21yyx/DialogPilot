import asyncio

from evaluation.graduation import GraduationDecision, GraduationStatus
from mcp.tool_manager import MCPToolManager, Tool, ToolEffectStatus, ToolRisk
from services.evolution import (
    AgentBundle,
    AgentBundleRegistry,
    RolloutManager,
    SoftRollbackPolicy,
)


def _setup(tmp_path, *, policy=None):
    registry = AgentBundleRegistry(str(tmp_path / "rollout.db"))
    base = AgentBundle(version="agent-v1", model_policy={"provider": "deepseek"})
    candidate = AgentBundle(
        version="agent-v2",
        base_version="agent-v1",
        prompts={"billing": "先核对订单再回答。"},
        model_policy={"provider": "deepseek"},
    )
    registry.bootstrap(base)
    registry.register(candidate, actor="evolution")
    rollout = RolloutManager(
        registry,
        bucket_salt="test-stable-salt",
        soft_policy=policy,
    )
    graduated = GraduationDecision(GraduationStatus.GRADUATED, "agent-v2")
    return registry, rollout, graduated


def test_shadow_canary_active_and_hard_rollback_are_atomic(tmp_path):
    registry, rollout, graduated = _setup(tmp_path)
    rollout.start_shadow("agent-v2", graduation=graduated, actor="reviewer")
    shadow_assignment = rollout.resolve("user-a")
    assert shadow_assignment.primary.version == "agent-v1"
    assert shadow_assignment.shadow.version == "agent-v2"

    rollout.promote_canary("agent-v2", percent=5, actor="release")
    assert rollout.resolve("sticky-user").bucket == rollout.resolve("sticky-user").bucket
    canary_buckets = sum(rollout._bucket(f"user-{index}") < 500 for index in range(10000))
    assert 450 <= canary_buckets <= 550
    rollout.promote_canary("agent-v2", percent=25, actor="release")
    rollout.promote_active("agent-v2", actor="release")
    in_flight = rollout.resolve("user-before-rollback")
    assert registry.active().version == "agent-v2"

    outcome = rollout.record_outcome(
        bundle_version="agent-v2",
        stage="active",
        verified=False,
        latency_ms=10,
        request_id="req-hard",
        hard_signal="privacy_leak",
    )
    assert outcome["active_version"] == "agent-v1"
    assert registry.active().version == "agent-v1"
    assert in_flight.primary.version == "agent-v2"


def test_soft_signal_waits_for_samples_then_rolls_back(tmp_path):
    policy = SoftRollbackPolicy(
        min_candidate_samples=10,
        min_baseline_samples=10,
        max_reject_rate_delta=0.05,
        sample_window=100,
    )
    registry, rollout, graduated = _setup(tmp_path, policy=policy)
    rollout.start_shadow("agent-v2", graduation=graduated, actor="reviewer")
    rollout.promote_canary("agent-v2", percent=5, actor="release")
    for index in range(10):
        rollout.record_outcome(
            bundle_version="agent-v1", stage="active", verified=True,
            latency_ms=100, request_id=f"base-{index}",
        )
    for index in range(9):
        assert rollout.record_outcome(
            bundle_version="agent-v2", stage="canary", verified=False,
            latency_ms=100, request_id=f"cand-{index}",
        ) is None
    rolled_back = rollout.record_outcome(
        bundle_version="agent-v2", stage="canary", verified=False,
        latency_ms=100, request_id="cand-9",
    )
    assert rolled_back["rolled_back"] == "agent-v2"
    assert registry.pointer("canary") is None
    assert registry.active().version == "agent-v1"


def test_shadow_write_tool_is_denied_before_handler(tmp_path):
    calls = []

    async def handler(params, context):
        calls.append(params)
        return {"ok": True}

    manager = MCPToolManager(api_key="test", model="test")
    manager.register(Tool(
        name="refund_create",
        description="创建退款",
        handler=handler,
        schema={"type": "object", "properties": {}},
        allowed_agents=("billing",),
        risk=ToolRisk.HIGH,
        read_only=False,
        requires_approval=True,
    ))
    result = asyncio.run(manager.execute_for_agent(
        "refund_create", {}, agent_type="billing",
        context={"execution_mode": "shadow", "request_id": "shadow-1"},
        approved=True,
    ))
    assert result.success is False
    assert result.status == "denied"
    assert result.effect_status == ToolEffectStatus.NOT_COMMITTED.value
    assert calls == []
