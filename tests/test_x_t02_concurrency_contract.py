"""X-T02 frozen ownership and fail-closed concurrency contract."""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_x_t02_contract_has_single_authorities_and_bounded_unknown_handling():
    contract = json.loads((
        ROOT / "governance/concurrency/x-t02-concurrency-contract-v1.json"
    ).read_text(encoding="utf-8"))

    assert contract["generation_contract"]["supported_active_cardinality"] == 1
    assert contract["tool_expiry_contract"]["write_invoking"] == (
        "expired invocation enters reconciling and cannot be re-invoked"
    )
    assert contract["tool_expiry_contract"]["unknown_receipt"] == (
        "fail closed in reconciling"
    )
    assert contract["authorities"]["tool_effect"] == "domain business system"
    assert contract["authorities"]["redis"] == (
        "discardable cache, single-flight and rate-limit coordination only"
    )


def test_x_t02_runbook_keeps_redis_non_authoritative_and_requires_primary_fencing():
    runbook = (
        ROOT / "docs/postgresql-redis-multi-replica-failover-runbook.zh-CN.md"
    ).read_text(encoding="utf-8")

    assert "STONITH" in runbook
    assert "仅权威 `NOT_COMMITTED` 允许再调用" in runbook
    assert "Redis 丢失不能触发 PostgreSQL 回退、SQLite 升主" in runbook
    assert "并发候选中最多一个成功" in runbook
