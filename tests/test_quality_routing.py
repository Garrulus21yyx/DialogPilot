"""Monitoring observes Target outcomes without becoming routing authority."""
from types import SimpleNamespace

from monitor.performance_monitor import PerformanceMonitor


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
