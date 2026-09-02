"""X-T04 online/offline budgets, typed exhaustion and usage reconciliation."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from application.cost_budget_policy import (
    OFFLINE_KNOWLEDGE_INGEST_BUDGET,
    RouteCostBudgetRegistry,
)
from application.route_decision import RouteMode
from core.cost_budget import (
    BudgetFallback,
    OfflineIngestBudget,
    OfflineIngestBudgetExceeded,
    ProviderUsageSample,
    RouteBudgetExceeded,
    RouteCostBudget,
    enforce_route_budget,
    reconcile_provider_usage,
)
from core.llm_metrics import capture_llm_usage, create_message
from core.model_policy import ModelProfile, ModelRole
from memory.context import TokenEstimator
from memory.hybrid_retrieval import HybridMemoryRetriever
from mcp.document_chunker import ChunkStrategy
from mcp.knowledge_base import KnowledgeBase
from mcp.sparse_index import PersistentBM25Index
from mcp.tool_manager import MCPToolManager, Tool
from scripts.create_x_t04_cost_manifest import build_manifest


ROOT = Path(__file__).resolve().parents[1]


def test_every_route_has_a_bounded_budget_and_deterministic_fallback():
    registry = RouteCostBudgetRegistry()
    rows = registry.all()

    assert {row.route_mode for row in rows} == {mode.value for mode in RouteMode}
    assert len({row.policy_version for row in rows}) == 1
    assert registry.for_route(RouteMode.MULTI_DOMAIN).max_model_calls == 14
    assert registry.for_route(RouteMode.AGENT_TASK).fallback is BudgetFallback.HANDOFF
    for mode in (
        RouteMode.DIRECT, RouteMode.CLARIFY, RouteMode.HANDOFF,
        RouteMode.OUT_OF_SCOPE,
    ):
        row = registry.for_route(mode)
        assert row.max_model_calls == row.max_tool_calls == row.max_retrieval_calls == 0


def test_model_call_limit_stops_next_provider_attempt_with_typed_outcome():
    class Messages:
        def __init__(self):
            self.calls = 0

        async def create(self, **_payload):
            self.calls += 1
            return SimpleNamespace(
                id=f"provider-{self.calls}", content=[],
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )

    budget = RouteCostBudget(
        "knowledge_qa", 1, 0, 1, 10, 10,
        BudgetFallback.ABSTAIN, "test-policy-v1",
    )
    messages = Messages()
    client = SimpleNamespace(messages=messages)
    profile = ModelProfile("deepseek-v4-flash", provider="deepseek")
    with enforce_route_budget(budget) as tracker, capture_llm_usage() as usage:
        asyncio.run(create_message(
            client, profile, ModelRole.SYNTHESIS,
            max_tokens=8, messages=[{"role": "user", "content": "one"}],
        ))
        with pytest.raises(RouteBudgetExceeded) as raised:
            asyncio.run(create_message(
                client, profile, ModelRole.SYNTHESIS,
                max_tokens=8, messages=[{"role": "user", "content": "two"}],
            ))

    outcome = tracker.outcome()
    assert messages.calls == 1
    assert raised.value.code == "ROUTE_COST_BUDGET_EXHAUSTED"
    assert outcome is not None
    assert outcome.dimension == "model_calls"
    assert outcome.fallback is BudgetFallback.ABSTAIN
    assert usage.calls[0].provider_request_id == "provider-1"


def test_provider_tokens_exhaust_budget_after_official_usage_without_extra_call():
    class Messages:
        async def create(self, **_payload):
            return SimpleNamespace(
                id="provider-over-token",
                content=[], usage=SimpleNamespace(input_tokens=11, output_tokens=2),
            )

    budget = RouteCostBudget(
        "knowledge_qa", 1, 0, 1, 10, 10,
        BudgetFallback.ABSTAIN, "test-policy-v1",
    )
    with enforce_route_budget(budget) as tracker:
        asyncio.run(create_message(
            SimpleNamespace(messages=Messages()),
            ModelProfile("deepseek-v4-flash", provider="deepseek"),
            ModelRole.SYNTHESIS,
            max_tokens=8, messages=[{"role": "user", "content": "one"}],
        ))

    assert tracker.outcome().dimension == "input_tokens"  # type: ignore[union-attr]
    assert tracker.outcome().observed == 11  # type: ignore[union-attr]


def test_tool_limit_blocks_handler_in_the_controlled_runtime():
    called = []

    async def handler(params, _context):
        called.append(params)
        return {"ok": True}

    runtime = MCPToolManager(api_key="x", model="x")
    runtime.register(Tool(
        name="budgeted_read", description="budget fixture", handler=handler,
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    budget = RouteCostBudget(
        "agent_task", 1, 0, 0, 100, 100,
        BudgetFallback.HANDOFF, "test-policy-v1",
    )
    with enforce_route_budget(budget) as tracker:
        result = asyncio.run(runtime.execute_for_agent(
            "budgeted_read", {}, agent_type="general",
        ))

    assert result.success is False
    assert called == []
    assert tracker.outcome().dimension == "tool_calls"  # type: ignore[union-attr]
    assert tracker.outcome().fallback is BudgetFallback.HANDOFF  # type: ignore[union-attr]


def test_offline_ingest_budget_is_independent_and_blocks_before_persistence():
    class Collection:
        def __init__(self):
            self.add_calls = 0

        def add(self, **_kwargs):
            self.add_calls += 1

    knowledge = KnowledgeBase.__new__(KnowledgeBase)
    knowledge._collection = Collection()
    knowledge._hybrid_retriever = HybridMemoryRetriever(recency_weight=0.0)
    knowledge._token_estimator = TokenEstimator()
    knowledge._chunk_max_tokens = 32
    knowledge._chunk_overlap_tokens = 0
    knowledge._chunk_strategy = ChunkStrategy.FIXED_TOKENS
    knowledge._sparse_index = PersistentBM25Index(":memory:")
    knowledge._offline_ingest_budget = OfflineIngestBudget(
        max_sources_per_batch=2, max_source_bytes=8, max_total_source_bytes=12,
        max_chunks_per_batch=10, max_embedding_tokens_per_batch=100,
        policy_version="offline-test-v1",
    )

    with pytest.raises(OfflineIngestBudgetExceeded) as raised:
        knowledge.add_documents([{
            "id": "too-large", "title": "large", "content": "0123456789",
            "scope": "public",
        }])

    assert raised.value.code == "OFFLINE_INGEST_BUDGET_EXHAUSTED"
    assert raised.value.dimension == "source_bytes"
    assert knowledge._collection.add_calls == 0

    with pytest.raises(OfflineIngestBudgetExceeded) as batch_raised:
        knowledge.add_documents([
            {"id": "one", "title": "one", "content": "1234567", "scope": "public"},
            {"id": "two", "title": "two", "content": "7654321", "scope": "public"},
        ])
    assert batch_raised.value.dimension == "total_source_bytes"
    assert knowledge._collection.add_calls == 0
    assert OFFLINE_KNOWLEDGE_INGEST_BUDGET.policy_version != (
        RouteCostBudgetRegistry.version
    )


def test_provider_usage_reconciliation_reports_exact_deltas():
    captured = ProviderUsageSample(calls=3, input_tokens=120, output_tokens=30)
    exact = reconcile_provider_usage(captured, captured)
    mismatch = reconcile_provider_usage(
        captured, ProviderUsageSample(calls=2, input_tokens=119, output_tokens=35),
    )

    assert exact.matches is True
    assert mismatch.matches is False
    assert (mismatch.call_delta, mismatch.input_token_delta, mismatch.output_token_delta) == (
        1, 1, -5,
    )


def test_cost_release_manifest_is_reproducible_and_requires_billing_sample():
    frozen = json.loads((
        ROOT / "evaluation/gates/x-t04-cost/v1.json"
    ).read_text(encoding="utf-8"))

    assert frozen == build_manifest()
    assert frozen["release_thresholds"]["provider_usage_reconciliation_rate"] == 1.0
    assert frozen["release_thresholds"]["provider_billing_sample_required"] is True
    assert frozen["evidence_status"] == (
        "BUILD_ONLY_PRODUCTION_BILLING_SAMPLE_REQUIRED"
    )
