"""Cross-role context properties: coverage, evidence ownership and wire admission."""
import asyncio
import copy
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from application.agent_result import FactRecord, FactSourceKind, AgentResultContractError
from application.response_evidence import composition_evidence, verification_evidence
from application.context_budget import ContextBudgetManager
from application.conversation_agent import ConversationAgent
from core.model_policy import ModelProfile, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_turn_context import TargetTurnContextLoader
from infrastructure.target_framework_agent import TargetFrameworkAgent, _adapt_framework_result
from infrastructure.target_agent_result_adapter import fact_from_tool_result
from infrastructure.target_model_context import delegated_working_input
from mcp.tool_manager import ToolResult
from tests.framework_structured_stub import models
from tests.test_target_turn_context import _identity, _state, Tools
from tests.test_target_framework_agent import _context, _item, _manager, ScriptedToolModel
from application.default_capability_registry import build_default_capability_registry
from langgraph.store.memory import InMemoryStore


@pytest.mark.parametrize("covered", [0, 10, 22, 250])
@pytest.mark.parametrize("limit", [1, 8, 20])
def test_window_removes_only_covered_messages(covered, limit):
    rows = [SimpleNamespace(seq=i * 2, role="user", content=f"restriction-{i}") for i in range(1, 125)]
    class Reader:
        async def get_projection_result(self, *args, **kwargs):
            return SimpleNamespace(state=SimpleNamespace(value="READY"), source_watermark=250,
                reason_codes=(), context=SimpleNamespace(recent_messages=rows,
                    summary="summary" if covered else "", summary_covered_until_seq=covered))
    from application.deterministic_resolution import TurnObservations, DeterministicResolver
    state, obs = _state(), TurnObservations("continue")
    result = asyncio.run(TargetTurnContextLoader(Reader(), Tools(), recent_limit=limit).load(
        _identity(), obs, state, DeterministicResolver().resolve(obs, state)))
    sent = {r.seq for r in result.recent_messages}
    assert all(r.seq in sent or r.seq <= covered for r in rows)
    assert {r.seq for r in rows[-limit:]} <= sent


def test_worker_receives_coordination_and_keeps_it_out_of_lossy_background():
    model = ScriptedToolModel(responses=[AIMessage(content="ok")])
    agent = TargetFrameworkAgent(model, _manager([]), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Specialist")
    view = {"siblings": [{"objective": "cancel other item", "state": "pending"}]}
    context = _context(assignment_view=view)
    content = agent._build_prompt(context)
    messages, pinned = delegated_working_input([], content, "execution")
    assert "cancel other item" in json.dumps(pinned.content)
    assert "cancel other item" not in json.dumps(messages[0].content)
    assert context.trusted_context["assignment_view"] == view


@pytest.mark.parametrize("has_required", [False, True])
def test_input_evidence_survives_without_granting_producer_permission(has_required):
    def fact(requirement):
        return FactRecord("order:1", requirement, '{"value":1}', FactSourceKind.VERIFIED_STATE,
            requirement, "prior-worker", "v1", datetime(2020, 1, 1, tzinfo=timezone.utc))
    facts = (fact("knowledge.active_source"),) + ((fact("order.current_state"),) if has_required else ())
    item = replace(_item(), requirement_ids=("order.current_state",))
    context = replace(_context(item), verified_facts=facts)
    result = _adapt_framework_result(context, (), "v1", allowed_authorities={"order_lookup": "order.current_state"},
                                     accepted_outcome={"kind": "COMPLETE"}, candidate_response="Completed.")
    assert result.facts == facts
    assert (result.status.value == "SUCCEEDED") == has_required


def test_missing_observation_time_never_becomes_fresh_evidence():
    with pytest.raises(AgentResultContractError, match="TOOL_OBSERVATION_TIME_MISSING"):
        fact_from_tool_result(_item(), ToolResult(True, {}, "catalog_search", authority="product.canonical_model"))


@pytest.mark.parametrize("reconcile", [False, True])
def test_missing_observation_does_not_erase_committed_receipt(reconcile):
    from infrastructure.target_workflow_execution import _ToolPort, _ToolReconciler
    class Source:
        read_reuse = None
        async def execute_for_agent(self, *args, **kwargs):
            return ToolResult(True, {"operation_key": "op", "receipt_id": "receipt"}, "write",
                effect_status="committed", receipt_id="receipt", receipt_schema_version="v1",
                authority="operation.state")
    item = SimpleNamespace(expected_output_schema="v1", arguments=(), reconciliation=SimpleNamespace(
        passthrough_arguments=(), operation_key_argument="operation_key", tool_id="reconcile",
        requirement_id="operation.state", operation_key_field="operation_key", receipt_id_field="receipt_id"))
    async def run():
        if reconcile:
            return await _ToolReconciler(Source(), _context(), principal="general").reconcile(item, operation_key="op")
        return await _ToolPort(Source(), _context(), principal="general").execute(
            item, tool_id="write", arguments={}, operation_key="op")
    result = asyncio.run(run())
    assert result.status.value == "COMMITTED"
    assert result.receipt_id == "receipt"
    assert result.facts == ()
    assert result.reason_code.endswith("OBSERVATION_UNAVAILABLE")


def test_worker_missing_observation_preserves_other_progress():
    prior = FactRecord("p", "product.canonical_model", '{"model":"PX"}', FactSourceKind.VERIFIED_STATE,
        "original", "catalog_search", "v1", datetime(2020, 1, 1, tzinfo=timezone.utc))
    context = replace(_context(), verified_facts=(prior,))
    result = _adapt_framework_result(context, (ToolResult(True, {}, "catalog_search",
        authority="product.canonical_model"),), "v1", allowed_authorities={"catalog_search": "product.canonical_model"})
    assert result.reason_code == "TOOL_OBSERVATION_TIME_MISSING"
    assert result.facts == (prior,)


def test_direct_missing_observation_preserves_prior_tool_results():
    from infrastructure.target_tool_execution import TargetToolExecutor
    from tests.test_target_framework_agent import _item, _context
    from application.work_item import ControlMode
    class Source:
        calls = 0
        async def execute_for_agent(self, name, *args, **kwargs):
            self.calls += 1
            return ToolResult(True, {"model":"PX"}, name, authority="product.canonical_model",
                observed_at=datetime(2020, 1, 1, tzinfo=timezone.utc) if self.calls == 1 else None,
                call_id=str(self.calls))
    item = replace(_item(allowed_tools=("media_read", "catalog_search")),
        control_mode=ControlMode.DELEGATED, skill_hint="product_identification",
        allowed_skills=("product_identification",))
    result = asyncio.run(TargetToolExecutor(Source(), registry=build_default_capability_registry("tenant-a"))(_context(item)))
    assert result.reason_code == "TOOL_OBSERVATION_TIME_MISSING"
    assert len(result.facts) == 1


@pytest.mark.parametrize("observed", [None, datetime(2020, 1, 1, tzinfo=timezone.utc)])
def test_product_skill_preserves_source_time_or_reports_missing_time(observed):
    from infrastructure.target_product_execution import TargetProductExecutor
    from tests.test_product_target_runtime import _context as product_context
    class Source:
        async def execute_for_agent(self, name, args, **kwargs):
            return ToolResult(True, {"text": "PX", "evidence_ref": "image:1"} if name == "media_read"
                else {"status": "MATCHED", "canonical_model": "PX"}, name,
                call_id="read:1", observed_at=observed, observation_started_at=observed)
    result = asyncio.run(TargetProductExecutor(Source())(product_context()))
    if observed is None:
        assert result.reason_code == "TOOL_OBSERVATION_TIME_MISSING"
        assert result.facts == ()
    else:
        assert result.facts[0].observed_at == observed
        assert result.facts[0].observation_started_at == observed


@pytest.mark.parametrize("project", [composition_evidence, verification_evidence])
def test_response_views_keep_scope_not_planning_instructions(project):
    fact = {"requirement_id": "order.current_state", "value": {"status": "pending"}, "source_ref": "read:1"}
    source = {"facts": [fact, copy.deepcopy(fact)], "outcomes": [{"fact_indexes": [1, 0]}],
        "pending_actions": [{"arguments": {"amount": 75.30}}],
        "user_context": {"observed_execution": {"contract": "DELEGATE AGAIN", "detail_contract": "CHOOSE TOOLS"},
                         "recent_messages": [{"role": "user", "content": "Do not purchase"}]}}
    before = copy.deepcopy(source)
    result = project(source)
    assert source == before
    assert len(result["facts"]) == 1
    assert result["outcomes"][0]["fact_indexes"] == [0]
    assert "DELEGATE AGAIN" not in json.dumps(result)
    assert "CHOOSE TOOLS" not in json.dumps(result)
    assert result["pending_actions"][0]["arguments"]["amount"] == 75.30
    assert "Do not purchase" in json.dumps(result)


def test_composer_admits_projected_request_not_archival_metadata():
    stub = models(text="Ready for your decision.", stop="end_turn")
    profile = ModelProfile("test")
    provider = AnthropicConversationPlanningProvider(stub, model_profile=profile, synthesis_profile=profile,
        synthesis_context_budget=ContextBudgetManager(context_window_tokens=3000,
            reserved_output_tokens=800, protocol_reserve_tokens=0))
    payload = {"current_message": "Ready?", "evidence": {"facts": [{"value": "pending", "source_ref": "x" * 60000}]}}
    before = copy.deepcopy(payload)
    assert asyncio.run(ConversationAgent(provider).compose(payload)) == "Ready for your decision."
    assert stub[ModelRole.SYNTHESIS].calls == 1
    assert payload == before


def test_response_overflow_compacts_only_history_and_preserves_snapshot(monkeypatch):
    from infrastructure.target_response_compaction import fit_response_context
    seen = []
    async def summary(model, messages, **kwargs):
        seen.extend(messages)
        return "Only compare; do not buy."
    monkeypatch.setattr("infrastructure.target_response_compaction.summarize_history", summary)
    source = {"evidence": {"pending_actions": [{"arguments": {"amount": 75.30}}],
        "user_context": {"recent_messages": [{"role": "user", "seq": i,
            "content": "Only compare; do not buy." * 100} for i in range(6)]}}}
    before = copy.deepcopy(source)
    result = asyncio.run(fit_response_context(source, target=6000, measure=lambda p: len(json.dumps(p)),
        model=object(), summary_available_tokens=10000))
    assert seen
    assert source == before
    assert result["evidence"]["pending_actions"] == source["evidence"]["pending_actions"]
    assert result["evidence"]["user_context"]["recent_messages"] == source["evidence"]["user_context"]["recent_messages"][-2:]
    assert len(json.dumps(result)) < len(json.dumps(source))


def test_no_history_recovery_handles_null_context_without_masking_capacity_error():
    from infrastructure.target_response_compaction import fit_response_context
    source = {"evidence": {"user_context": None, "facts": [{"value": "x" * 2000}]}}
    result = asyncio.run(fit_response_context(source, target=500, measure=lambda p: len(json.dumps(p)),
        model=object(), summary_available_tokens=1000))
    assert result == source


def test_recovery_target_is_not_the_hard_capacity(monkeypatch):
    from infrastructure.target_response_compaction import fit_response_context
    async def summary(*args, **kwargs):
        return "saved choice"
    monkeypatch.setattr("infrastructure.target_response_compaction.summarize_history", summary)
    source = {"evidence": {"user_context": {"recent_messages": [
        {"role": "user", "seq": i, "content": "old request"} for i in range(3)]}}}
    def measure(p):
        context = p["evidence"]["user_context"]
        return 900 + (300 if len(context["recent_messages"]) > 2 else 50 if context.get("summary") else 0)
    result = asyncio.run(fit_response_context(source, target=800, measure=measure,
        model=object(), summary_available_tokens=1000))
    assert measure(source) == 1200
    assert measure(result) == 950
