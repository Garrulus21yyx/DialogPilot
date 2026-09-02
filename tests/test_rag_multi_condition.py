from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from core.model_policy import ModelProfile
from evaluation.retrieval_eval_utils import (
    cascade_replay,
    round_robin_union,
    select_requirements,
)
from mcp.evidence_set_selector import (
    EvidenceSetSelector,
    _EvidenceSetDeps,
    _RequirementSelection,
    _StructuredEvidenceSetOutput,
)
from mcp.query_requirements import (
    QueryRequirementPlanner,
    RetrievalRequirement,
    _RequirementDeps,
    _StructuredRequirementOutput,
)
from mcp.result_reranker import RerankCandidate


def test_round_robin_union_preserves_every_requirement_before_depth():
    result = round_robin_union({
        "R1": ["a", "shared", "c"],
        "R2": ["b", "shared", "d"],
        "R0": ["raw", "a", "e"],
    }, ("R1", "R2", "R0"), limit=5)

    assert result == ["a", "b", "raw", "shared", "c"]
    assert len(result) == len(set(result))


def test_requirement_contract_rejects_dropped_identifier_and_negation():
    deps = _RequirementDeps(("A123",), ("not",))
    invalid = _StructuredRequirementOutput(
        query_kind="parallel_composite",
        requirements=["How do I return it?", "When is the refund?"],
    )

    with pytest.raises(ModelRetry):
        QueryRequirementPlanner._validate_output(SimpleNamespace(deps=deps), invalid)


def test_requirement_planner_fails_to_identity_plan_without_losing_raw():
    class FailingAgent:
        async def run(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    result = asyncio.run(QueryRequirementPlanner(
        object(), ModelProfile("test-model"), structured_agent=FailingAgent(),
    ).plan("Can I return A123?", "Can I return order A123?"))

    assert result.error == "RuntimeError"
    assert result.query_kind == "simple"
    assert result.retrieval_queries[0] == RetrievalRequirement("R0", "Can I return A123?")
    assert result.requirements == (
        RetrievalRequirement("R1", "Can I return order A123?"),
    )


def test_evidence_set_contract_requires_total_requirement_partition_and_known_ids():
    invalid = _StructuredEvidenceSetOutput(
        selections=[_RequirementSelection(
            requirement_id="R1", candidate_ids=["C99"],
        )],
        missing_requirement_ids=[],
    )
    deps = _EvidenceSetDeps(("R1", "R2"), ("C01", "C02"), 5)

    with pytest.raises(ModelRetry):
        EvidenceSetSelector._validate_output(SimpleNamespace(deps=deps), invalid)


def test_evidence_set_fallback_keeps_one_anchor_per_requirement_before_fill():
    selected, assignments, missing = EvidenceSetSelector._fallback(
        (RetrievalRequirement("R1", "refund"), RetrievalRequirement("R2", "shipping")),
        ("a", "b", "c", "d"),
        {"R1": ["c", "a"], "R2": ["b", "d"]},
        max_candidates=3,
    )

    assert selected == ("c", "b", "a")
    assert assignments == {"R1": ("c",), "R2": ("b",)}
    assert missing == ()


def test_evidence_selector_maps_aliases_back_to_stable_ids():
    output = _StructuredEvidenceSetOutput(
        selections=[
            _RequirementSelection(requirement_id="R1", candidate_ids=["C02"]),
            _RequirementSelection(requirement_id="R2", candidate_ids=["C01"]),
        ],
        missing_requirement_ids=[],
    )

    class FakeAgent:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(output=output)

    result = asyncio.run(EvidenceSetSelector(
        object(), ModelProfile("test-model"), structured_agent=FakeAgent(),
    ).select(
        "refund and shipping",
        (RetrievalRequirement("R1", "refund"), RetrievalRequirement("R2", "shipping")),
        (RerankCandidate("stable-a", "shipping"), RerankCandidate("stable-b", "refund")),
        fallback_rankings={"R1": ["stable-b"], "R2": ["stable-a"]},
    ))

    assert result.selected_ids == ("stable-b", "stable-a")
    assert result.assignments == {"R1": ("stable-b",), "R2": ("stable-a",)}
    assert result.error is None


def test_cross_encoder_selection_reserves_one_anchor_per_requirement():
    class FakeCrossEncoder:
        def predict(self, pairs, **_kwargs):
            values = {
                ("refund", "refund evidence"): 9.0,
                ("refund", "shipping evidence"): 1.0,
                ("refund", "general evidence"): 5.0,
                ("shipping", "refund evidence"): 2.0,
                ("shipping", "shipping evidence"): 8.0,
                ("shipping", "general evidence"): 4.0,
            }
            return [values[pair] for pair in pairs]

    result = select_requirements(FakeCrossEncoder(), {
        "requirements": (
            RetrievalRequirement("R1", "refund"),
            RetrievalRequirement("R2", "shipping"),
        ),
        "fused_ids": ["a", "b", "c"],
        "hit_by_id": {
            "a": {"content": "refund evidence"},
            "b": {"content": "shipping evidence"},
            "c": {"content": "general evidence"},
        },
    })

    assert result["reranked_ids"][:2] == ["a", "b"]
    assert result["selection_assignments"] == {"R1": ["a"], "R2": ["b"]}
    assert result["rerank_usage"]["input_tokens"] == 0


def test_low_margin_cascade_replay_charges_fallback_and_uses_llm_outcome():
    cross_rows = [{
        "case": SimpleNamespace(case_id=f"c{index}"),
        "cross_encoder_cutoff_margin": float(index),
        "context_recall": 0.0,
        "rerank_usage": {"latency_ms": {"sum": 10.0}},
    } for index in range(10)]
    llm_rows = [{
        "case_id": f"c{index}",
        "packed_recall": 1.0,
        "selection_usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "latency_ms": {"sum": 50.0},
        },
    } for index in range(10)]

    replay = cascade_replay(cross_rows, llm_rows)
    ten_percent = next(row for row in replay if row["fallback_count"] == 1)

    assert ten_percent["packed_recall"] == pytest.approx(0.1)
    assert ten_percent["llm_input_tokens"] == 100
    assert ten_percent["llm_output_tokens"] == 10
    assert ten_percent["p95_selection_latency_ms"] > 10.0
