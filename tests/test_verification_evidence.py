"""Reviewer projections preserve facts and coverage without execution instructions."""
import asyncio
import copy
import itertools
import json

from application.response_evidence import verification_evidence
from core.model_policy import ModelProfile
from services.claim_verification import verify_claims


def test_projection_preserves_all_outcomes_and_remaps_duplicate_evidence():
    for order in itertools.permutations([0, 1, 2]):
        facts = [{"value": {"price": 272.33}, "source_ref": "catalog"},
                 {"value": {"price": 272.33}, "source_ref": "catalog"},
                 {"value": {"price": 235.13}, "source_ref": "order"}]
        original = {"facts": [facts[i] for i in order],
            "outcomes": [{"requested_objective": "compare", "fact_indexes": [0, 1, 2],
                          "control": {"revision": 7}, "coverage": {"complete": False}},
                         {"requested_objective": "other goal", "fact_indexes": []}],
            "pending_actions": [], "receipts": [{"effect_status": "committed"}],
            "user_context": {"recent_messages": [{"content": "Only compare; don't buy."}],
                             "retained_approval": {"state": "pending"}},
            "capability_policy": {"agents": [{"description": "EXECUTION-INSTRUCTION",
                "conversation_policy": "Authenticate before disclosing customer records."}],
                "business_actions": [{"tool_id": "modify", "description": "Pending orders only."}]}}
        before = copy.deepcopy(original)
        view = verification_evidence(original)
        assert original == before
        assert len(view["facts"]) == 2
        assert len(view["outcomes"]) == 2
        assert len(view["outcomes"][0]["fact_indexes"]) == 2
        assert all(view["facts"][i] in facts for i in view["outcomes"][0]["fact_indexes"])
        assert view["receipts"] == original["receipts"]
        assert view["user_context"] == original["user_context"]
        assert "EXECUTION-INSTRUCTION" not in json.dumps(view)
        assert view["policy_excerpts"][0]["text"] == "Pending orders only."
        assert view["conversation_constraints"] == ["Authenticate before disclosing customer records."]


def test_history_scope_waits_and_unavailable_evidence_are_not_lost():
    original = {"outcomes": [{"requested_evidence": [{"requirement_id": "policy",
        "arguments": {"region": "NY"}, "preferred_providers": ["search"]}]}],
        "pending_actions": [{"action": "change", "arguments": {"address": "New address"}}],
        "requested_inputs": [{"field": "payment_method"}],
        "user_context": {"observed_execution": {"contract": "Choose tools",
            "outcomes": [{"task_input": {"control_mode": "DIRECT", "tool": "lookup",
                                           "arguments": {"user_id": "U1"}}}]},
            "knowledge_evidence": [{"status": "NOT_REUSABLE", "pack": {"old": "policy"}}]}}
    view = verification_evidence(original)
    assert view["outcomes"][0]["requested_evidence"] == [{"requirement_id": "policy", "arguments": {"region": "NY"}}]
    assert view["pending_actions"] == original["pending_actions"]
    assert view["requested_inputs"] == original["requested_inputs"]
    assert view["user_context"]["knowledge_evidence"] == original["user_context"]["knowledge_evidence"]
    assert view["user_context"]["observed_execution"]["outcomes"][0]["observation_scope"] == {
        "tool": "lookup", "arguments": {"user_id": "U1"}}


def test_model_gets_projected_view_but_assessment_binds_complete_snapshot(monkeypatch):
    captured = {}
    async def invoke(*args, **kwargs):
        captured.update(kwargs)
        return {"supported": True, "answered": True, "issues": []}
    monkeypatch.setattr("services.claim_verification.structured_call", invoke)
    evidence = {"context": {"facts": [], "pending_actions": [],
        "user_context": {"observed_execution": {"contract": "PLAN AND RETRY",
                                                "facts": [{"value": "retained evidence"}]}}}}
    result = asyncio.run(verify_claims(object(), ModelProfile("test"),
        question="Which method?", answer="Which payment method would you like?", evidence=evidence))
    assert result.matches("Which method?", "Which payment method would you like?", evidence)
    wire = captured["messages"][0].content
    assert "PLAN AND RETRY" not in wire
    assert "retained evidence" in wire
    assert "Presentation contract:" not in captured["system"]
    assert set(captured["schema"]["properties"]) == {"supported", "answered", "issues"}


def test_knowledge_body_has_one_model_copy_without_losing_citation_scope(monkeypatch):
    captured = {}
    async def invoke(*args, **kwargs):
        captured.update(kwargs)
        return {"supported": True, "answered": True, "issues": []}
    monkeypatch.setattr("services.claim_verification.structured_call", invoke)
    pack = {"evidence": [{"id": "E1", "text": "UNIQUE_POLICY_BODY"}]}
    evidence = {"context": {"facts": [{"requirement_id": "knowledge.active_source", "value": pack}]},
        "knowledge_evidence": {"packs": [pack], "allowed_evidence_ids": ["E1"]}}
    result = asyncio.run(verify_claims(object(), ModelProfile("test"), question="Rule?", answer="Policy [E1]", evidence=evidence))
    wire = captured["messages"][0].content
    assert wire.count("UNIQUE_POLICY_BODY") == 1
    assert "context_fact_index" in wire and "allowed_evidence_ids" in wire
    assert result.matches("Rule?", "Policy [E1]", evidence)
