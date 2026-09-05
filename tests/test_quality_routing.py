from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import AgentOrchestrator, AgentStats, AgentType
from monitor.performance_monitor import PerformanceMonitor


def test_unknown_verification_is_observed_without_penalizing_quality():
    """证明 UNKNOWN 可观测但不错误惩罚 Agent 内容质量。"""
    stats = AgentStats(total=10, success=10, total_ms=1000)
    before_quality = stats.quality_score
    before_routing = stats.routing_score()

    stats.record_verification("unknown")

    assert stats.verification_unknown == 1
    assert stats.quality_samples == 0
    assert stats.quality_score == before_quality
    assert stats.routing_score() == before_routing


def test_sample_aware_ewma_moves_gradually_for_pass_and_reject():
    """证明带先验收缩的 EWMA 会随 PASS/REJECT 样本渐进变化。"""
    passing = AgentStats(total=10, success=10, total_ms=1000)
    failing = AgentStats(total=10, success=10, total_ms=1000)

    passing.record_verification("pass")
    failing.record_verification("reject")

    assert 0.5 < passing.quality_score < 1.0
    assert 0.0 < failing.quality_score < 0.5
    assert passing.routing_score() > failing.routing_score()
    assert passing.verified_pass == 1
    assert failing.verified_reject == 1


def test_quality_feedback_changes_selection_between_same_type_instances():
    """证明同类型实例可以因经验证质量差异改变选择结果。"""
    low = fake_agent("technical_low")
    high = fake_agent("technical_high")
    for _ in range(10):
        low.stats.record_verification("reject")
        high.stats.record_verification("pass")

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {AgentType.TECHNICAL: [low, high]}

    assert orchestrator._best_agent(AgentType.TECHNICAL) is high


def test_feedback_is_attributed_only_to_named_producer_instances():
    """证明质量反馈只归因给实际生产候选回答的实例。"""
    general = fake_agent("general_0")
    technical = fake_agent("technical_0")
    billing = fake_agent("billing_0")
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [general],
        AgentType.TECHNICAL: [technical],
        AgentType.BILLING: [billing],
    }

    orchestrator.record_verification(["technical_0", "billing_0"], "reject")

    assert general.stats.quality_samples == 0
    assert technical.stats.verified_reject == 1
    assert billing.stats.verified_reject == 1


def test_stats_disclose_when_adaptive_routing_has_no_alternative():
    """证明单实例池不会宣称在线降权能够改选其他实例。"""
    only = fake_agent("technical_0")
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {AgentType.TECHNICAL: [only]}

    stats = orchestrator.get_stats()["technical_0"]

    assert stats["routing_pool_size"] == 1
    assert stats["adaptive_routing_active"] is False


def test_monitor_observes_target_results_without_changing_routing():
    def must_not_change_routing(*_args):
        raise AssertionError("monitor observations cannot change routing")

    stats = {
        "product_technical": {
            "success_rate": 0.5, "outcome_samples": 20, "total": 20, "avg_ms": 100,
        },
        "billing_refund": {
            "success_rate": None, "outcome_samples": 0, "total": 1, "avg_ms": 100,
        },
    }
    runtime = SimpleNamespace(
        get_stats=lambda: stats, update_routing_penalties=must_not_change_routing,
    )
    monitor = PerformanceMonitor(runtime, SimpleNamespace(get_stats=lambda: {}))

    import asyncio
    asyncio.run(monitor._collect())

    summary = monitor.summary()
    assert summary["agent_stats"] == stats
    assert len(summary["suggestions"]) == 1
    assert all("billing_refund" not in item["metric"] for item in summary["active_alerts"])


def test_unsupported_feedback_status_fails_deterministically():
    """证明未支持的反馈状态以 ValueError 确定性失败。"""
    stats = AgentStats()
    with pytest.raises(ValueError):
        stats.record_verification("maybe")


def fake_agent(instance_id):
    return SimpleNamespace(
        instance_id=instance_id,
        stats=AgentStats(total=20, success=20, total_ms=2000),
    )
"""经发布校验的质量反馈及其路由归因测试。"""
