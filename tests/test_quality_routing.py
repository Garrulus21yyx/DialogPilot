from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import AgentOrchestrator, AgentStats, AgentType


def test_unknown_verification_is_observed_without_penalizing_quality():
    stats = AgentStats(total=10, success=10, total_ms=1000)
    before_quality = stats.quality_score
    before_routing = stats.routing_score()

    stats.record_verification("unknown")

    assert stats.verification_unknown == 1
    assert stats.quality_samples == 0
    assert stats.quality_score == before_quality
    assert stats.routing_score() == before_routing


def test_sample_aware_ewma_moves_gradually_for_pass_and_reject():
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
    low = fake_agent("technical_low")
    high = fake_agent("technical_high")
    for _ in range(10):
        low.stats.record_verification("reject")
        high.stats.record_verification("pass")

    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {AgentType.TECHNICAL: [low, high]}

    assert orchestrator._best_agent(AgentType.TECHNICAL) is high


def test_feedback_is_attributed_only_to_named_producer_instances():
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


def test_unsupported_feedback_status_fails_deterministically():
    stats = AgentStats()
    with pytest.raises(ValueError):
        stats.record_verification("maybe")


def fake_agent(instance_id):
    return SimpleNamespace(
        instance_id=instance_id,
        stats=AgentStats(total=20, success=20, total_ms=2000),
    )
