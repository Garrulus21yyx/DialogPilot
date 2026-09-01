from types import SimpleNamespace

from evaluation.rag_context_topology_ablation import (
    _aggregate,
    _build_parents,
    _candidate_gates,
    _conditional_captures,
    _conditional_route,
    _context_recall,
    _make_contexts,
    _query_contract,
    _reuse_reranks,
    _structural_gates,
)
from evaluation.rag_pipeline.contracts import EvidenceSpan, RagCase, RagDocument
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import project_chunks


def dataset(content: str) -> RagDataset:
    document = RagDocument("policy", "Policy", content)
    return RagDataset(
        root=None,
        manifest={"dataset_id": "test"},
        documents=(document,),
        cases=(),
    )


def test_parent_records_keep_global_source_coordinates():
    value = dataset("refund rules. " * 900)
    sources, parents = _build_parents(value)

    assert len(sources) > 1
    assert all(parent.source_id == "policy" for parent in parents.values())
    assert all(
        value.documents[0].content[parent.start_char:parent.end_char] == parent.text
        for parent in parents.values()
    )


def test_neighbor_expansion_covers_gold_outside_the_anchor():
    value = dataset("A " * 3000)
    chunks = project_chunks(
        value.documents, max_tokens=512, overlap_tokens=64, strategy="fixed_tokens",
    )
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    by_document = {"policy": chunks}
    anchor = chunks[1]
    capture = {
        "reranked_ids": [anchor.chunk_id],
        "hit_by_id": {anchor.chunk_id: {"title": "Policy", "score": 1.0}},
    }
    contexts = _make_contexts(
        "neighbor-512", capture, value, by_id, by_document,
    )
    gold = EvidenceSpan("policy", chunks[0].start_char, chunks[0].end_char)
    case = RagCase("c", "g", "dev", "q", evidence=(gold,))

    assert _context_recall(case, contexts) == 1.0
    assert contexts[0].start_char == chunks[0].start_char


def test_candidate_selection_requires_every_non_compensable_gate():
    baseline = {
        "candidate_evidence_recall_at_20": 0.8,
        "reranked_mrr_at_5": 0.5,
        "multi_condition_context_completeness": 0.6,
        "claim_support_rate": 0.9,
        "citation_correctness_rate": 0.9,
        "p95_context_tokens": 2000,
        "p95_pipeline_latency_ms": 1000,
        "rerank_failure_rate": 0.0,
        "generation_failure_rate": 0.0,
        "judge_failure_rate": 0.0,
    }
    candidate = {**baseline, "multi_condition_context_completeness": 0.7}
    assert all(_candidate_gates(candidate, baseline).values())

    candidate["claim_support_rate"] = 0.89
    assert _candidate_gates(candidate, baseline)["claim_support_not_lower"] is False


def test_structural_precheck_requires_measurable_context_gain():
    baseline = {
        "candidate_evidence_recall_at_20": 0.8,
        "first_stage_mrr_at_5": 0.5,
        "multi_condition_context_completeness": 0.6,
        "p95_context_tokens": 2000,
        "p95_retrieval_latency_ms": 100,
    }
    unchanged = dict(baseline)
    assert _structural_gates(unchanged, baseline, harmful_rate=0.0)[
        "multi_condition_completeness_improves"
    ] is False

    improved = {**baseline, "multi_condition_context_completeness": 0.7}
    assert _structural_gates(improved, baseline, harmful_rate=0.0)["passes_all"] is True


def test_generation_failure_cannot_be_hidden_by_judging_the_fallback():
    case = RagCase(
        "c", "g", "dev", "q",
        evidence=(EvidenceSpan("policy", 0, 1),),
    )
    row = {
        "case": case,
        "retrieval_metrics": {"evidence_recall": 1.0},
        "reranked_metrics": {"mrr": 1.0, "evidence_recall": 1.0},
        "context_recall": 1.0,
        "context_tokens": 10,
        "retrieval_latency_ms": 1.0,
        "rerank_usage": {"latency_ms": {"sum": 1.0}},
        "generation_usage": {"latency_ms": {"sum": 1.0}},
        "judge_usage": {"latency_ms": {"sum": 1.0}},
        "rerank_error": None,
        "generation_error": "ValueError",
        "abstained": True,
        "judge": {"grounded": True, "citations_relevant": True, "complete": True},
    }

    summary = _aggregate("baseline-512", [row])

    assert summary["generation_failure_rate"] == 1.0
    assert summary["claim_support_rate"] == 0.0
    assert summary["citation_correctness_rate"] == 0.0


def test_typed_evidence_abstention_is_not_a_generation_contract_failure():
    case = RagCase(
        "c", "g", "dev", "q",
        evidence=(EvidenceSpan("policy", 0, 1),),
    )
    row = {
        "case": case,
        "retrieval_metrics": {"evidence_recall": 0.0},
        "reranked_metrics": {"mrr": 0.0, "evidence_recall": 0.0},
        "context_recall": 0.0,
        "context_tokens": 10,
        "retrieval_latency_ms": 1.0,
        "rerank_usage": {"latency_ms": {"sum": 1.0}},
        "generation_usage": {"latency_ms": {"sum": 1.0}},
        "judge_usage": {"latency_ms": {"sum": 0.0}},
        "rerank_error": None,
        "generation_error": None,
        "abstained": True,
        "judge": None,
    }

    summary = _aggregate("baseline-512", [row])

    assert summary["generation_failure_rate"] == 0.0
    assert summary["abstention_rate"] == 1.0


def test_rerank_resume_requires_the_exact_candidate_set():
    case = SimpleNamespace(case_id="c")
    capture = {"case": case, "fused_ids": ["a", "b"]}
    previous = [{
        "case_id": "c",
        "candidate_ids": ["a", "b"],
        "reranked_ids": ["b", "a"],
        "rerank_error": None,
        "rerank_usage": {"calls": 1},
    }]
    assert _reuse_reranks([capture], previous)[0]["reranked_ids"] == ["b", "a"]

    previous[0]["candidate_ids"] = ["b", "a"]
    assert _reuse_reranks([capture], previous)[0]["reranked_ids"] == ["b", "a"]

    previous[0]["rerank_error"] = "ValueError"
    assert _reuse_reranks([capture], previous)[0]["reranked_ids"] == ["a", "b"]

    previous[0]["candidate_ids"] = ["a", "changed"]
    try:
        _reuse_reranks([capture], previous)
    except ValueError as exc:
        assert "candidates differ" in str(exc)
    else:
        raise AssertionError("a different candidate set must not be reused")


def test_query_contract_reports_raw_only_capture_without_claiming_a_rewrite():
    raw = {"rows": [{"raw_query": "refund?", "standalone": ""}]}
    rewritten = {"rows": [{
        "raw_query": "When?",
        "standalone": "When will the refund arrive?",
    }]}

    assert _query_contract(raw).startswith("Raw-only 1.0")
    assert _query_contract(rewritten).startswith("Raw .25 + captured Standalone .75")


def test_conditional_route_uses_only_observable_length_and_branch_agreement():
    capture = {
        "fused_ids": ["a", "b"],
        "source_rankings": {"raw:bm25": ["a"], "raw:vector": ["b"]},
        "hit_by_id": {
            "a": {"document_id": "long"},
            "b": {"document_id": "short"},
        },
    }
    routed = _conditional_route(capture, {"long": 8000, "short": 100})
    assert routed["selected_topology"] == "parent-child-256-1024"

    capture["source_rankings"]["raw:vector"] = ["a"]
    agreed = _conditional_route(capture, {"long": 8000, "short": 100})
    assert agreed["selected_topology"] == "baseline-512"

    capture["source_rankings"]["raw:vector"] = ["b"]
    short = _conditional_route(capture, {"long": 7999, "short": 100})
    assert short["selected_topology"] == "baseline-512"


def test_conditional_cascade_charges_baseline_and_parent_retrieval_latency():
    case = SimpleNamespace(case_id="case")
    value = dataset("long policy " * 1000)
    baseline = [{
        "case": case,
        "fused_ids": ["a", "b"],
        "source_rankings": {"raw:bm25": ["a"], "raw:vector": ["b"]},
        "hit_by_id": {
            "a": {"document_id": "policy"},
            "b": {"document_id": "other"},
        },
        "retrieval_latency_ms": 10.0,
    }]
    parent = [{
        "case": case,
        "fused_ids": ["parent"],
        "source_rankings": {},
        "hit_by_id": {"parent": {"document_id": "policy"}},
        "retrieval_latency_ms": 15.0,
    }]

    selected = _conditional_captures(value, baseline, parent)

    assert selected[0]["fused_ids"] == ["parent"]
    assert selected[0]["retrieval_latency_ms"] == 25.0
    assert selected[0]["route_decision"]["selected_topology"] == "parent-child-256-1024"
