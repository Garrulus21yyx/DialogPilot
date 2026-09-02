import asyncio

import pytest

from evaluation.graduation import GraduationDecision, GraduationStatus
from mcp.tool_manager import MCPToolManager, Tool, ToolEffectStatus, ToolRisk
from services.evolution import (
    AgentBundle,
    AgentBundleRegistry,
    RolloutManager,
    RolloutState,
    SoftRollbackPolicy,
)


def _setup(tmp_path, *, policy=None, **rollout_kwargs):
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
        **rollout_kwargs,
    )
    graduated = GraduationDecision(GraduationStatus.GRADUATED, "agent-v2")
    return registry, rollout, graduated


def _concrete_refs(bundle, generation_by_version):
    generation = generation_by_version[bundle.version]
    return {
        "route_policy_ref": "route-contract-v1",
        "knowledge_backend_ref": "POSTGRES_HYBRID_V1",
        "knowledge_generation_ref": generation,
        "corpus_manifest_ref": f"manifest:{generation}",
        "retrieval_policy_ref": bundle.component_hash("retrieval_policy"),
    }


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


def test_assignment_pins_bundle_route_and_knowledge_refs_once(tmp_path):
    generations = {"agent-v1": "generation-1", "agent-v2": "generation-2"}
    registry, rollout, graduated = _setup(
        tmp_path,
        execution_ref_resolver=lambda bundle: _concrete_refs(bundle, generations),
        strict_execution_refs=True,
    )
    rollout.start_shadow("agent-v2", graduation=graduated, actor="reviewer")
    first = rollout.resolve("user")
    generations["agent-v1"] = "generation-replaced"
    generations["agent-v2"] = "generation-replaced"
    replay = rollout.resolve("user")

    assert first.publisher_version == "agent-v1"
    assert first.pinned_refs == replay.pinned_refs
    assert first.pinned_refs.knowledge_generation_ref == "generation-1"
    assert first.shadow_pinned_refs.knowledge_generation_ref == "generation-2"
    assert first.pinned_refs.fingerprint == replay.pinned_refs.fingerprint


def test_incompatible_previous_blocks_admission_instead_of_forced_rollback(tmp_path):
    generations = {"agent-v1": "generation-1", "agent-v2": "generation-2"}
    available = {"generation-1", "generation-2"}
    registry, rollout, graduated = _setup(
        tmp_path,
        execution_ref_resolver=lambda bundle: _concrete_refs(bundle, generations),
        execution_ref_validator=lambda refs: (
            () if refs.knowledge_generation_ref in available
            else ("knowledge generation unavailable",)
        ),
        strict_execution_refs=True,
    )
    rollout.start_shadow("agent-v2", graduation=graduated, actor="reviewer")
    rollout.promote_canary("agent-v2", percent=5, actor="release")
    rollout.promote_canary("agent-v2", percent=25, actor="release")
    rollout.promote_active("agent-v2", actor="release")
    available.remove("generation-1")

    outcome = rollout.rollback(
        "agent-v2", actor="incident-commander", reason={"hard_signal": "wrong_write"},
    )
    assignment = rollout.resolve("new-user")

    assert outcome["rollback_blocked"] is True
    assert outcome["safe_disposition"] == "typed_fail_closed_or_handoff"
    assert rollout.status("agent-v2")["state"] == (
        RolloutState.BLOCKED_FORWARD_FIX.value
    )
    assert registry.active().version == "agent-v2"
    assert assignment.admission_allowed is False
    assert assignment.publisher_version == ""

    forward_fix = AgentBundle(
        version="agent-v3", base_version="agent-v2",
        model_policy={"provider": "deepseek"},
    )
    registry.register(forward_fix, actor="incident-commander")
    generations["agent-v3"] = "generation-3"
    available.add("generation-3")
    rollout.start_shadow(
        "agent-v3",
        graduation=GraduationDecision(GraduationStatus.GRADUATED, "agent-v3"),
        actor="reviewer",
    )
    rollout.promote_canary("agent-v3", percent=5, actor="release")
    rollout.promote_canary("agent-v3", percent=25, actor="release")
    rollout.promote_active("agent-v3", actor="release")

    recovered = rollout.resolve("new-user")
    assert recovered.publisher_version == "agent-v3"
    assert recovered.admission_allowed is True
    assert rollout.status("agent-v2")["state"] == RolloutState.RETIRED.value


@pytest.mark.parametrize(
    "fault_point",
    ["promote_active.before_pointer_swap", "rollback.after_admission_stop"],
)
def test_rollout_crash_never_commits_partial_pointer_or_state(tmp_path, fault_point):
    armed = {"value": False}

    def fault(point):
        if armed["value"] and point == fault_point:
            raise RuntimeError("simulated process crash")

    registry, rollout, graduated = _setup(tmp_path, fault_hook=fault)
    rollout.start_shadow("agent-v2", graduation=graduated, actor="reviewer")
    rollout.promote_canary("agent-v2", percent=5, actor="release")
    rollout.promote_canary("agent-v2", percent=25, actor="release")
    if fault_point == "rollback.after_admission_stop":
        rollout.promote_active("agent-v2", actor="release")
    armed["value"] = True

    with pytest.raises(RuntimeError, match="simulated process crash"):
        if fault_point == "promote_active.before_pointer_swap":
            rollout.promote_active("agent-v2", actor="release")
        else:
            rollout.rollback("agent-v2", actor="release", reason={"test": True})

    if fault_point == "promote_active.before_pointer_swap":
        assert registry.active().version == "agent-v1"
        assert rollout.status("agent-v2")["state"] == RolloutState.CANARY.value
        assert rollout.status("agent-v1")["state"] == RolloutState.ACTIVE.value
    else:
        assert registry.active().version == "agent-v2"
        assert rollout.status("agent-v2")["state"] == RolloutState.ACTIVE.value
        assert rollout.status("agent-v1")["state"] == RolloutState.RETIRED.value
