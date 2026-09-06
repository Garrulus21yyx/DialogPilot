import pytest
from evaluation.rag_acceptance_decision import exact_paired_p


@pytest.mark.parametrize(
    "rescued,harmed,p",
    [(0, 0, 1), (5, 0, 0.0625), (6, 0, 0.03125), (0, 6, 0.03125), (3, 3, 1)],
)
def test_exact_paired_significance(rescued, harmed, p):
    assert exact_paired_p(rescued, harmed) == pytest.approx(p)


def test_decision_recomputes_evidence_and_rejects_capture_drift():
    from copy import deepcopy
    from types import SimpleNamespace
    from evaluation.rag_acceptance_decision import decide

    dataset = SimpleNamespace(
        documents=[SimpleNamespace(document_id="d", content="abcdef")],
        cases=[
            SimpleNamespace(
                case_id="c",
                group_id="g",
                evidence=[SimpleNamespace(document_id="d", start_char=0, end_char=3)],
            )
        ],
    )
    row = {
        "case_id": "c",
        "query": "q",
        "fused": ["chunk"],
        "metrics": {"tool_message_complete": True},
        "tool_message": {
            "evidence": [
                {
                    "text": "abc",
                    "source": {"source_id": "d", "start_char": 0, "end_char": 3},
                }
            ]
        },
    }
    contract = {
        "success_criteria": {"paired_exact_mcnemar_two_sided_p_less_than": 0.05},
        "decision_scope": "local",
    }
    result = decide(dataset, [row], [deepcopy(row)], contract)
    assert result["baseline_complete"] == result["reranked_complete"] == 1
    assert result["decision"] == "EVIDENCE_QUALITY_NOT_DEMONSTRATED"
    bad = deepcopy(row)
    bad["metrics"]["tool_message_complete"] = False
    with pytest.raises(ValueError, match="reported metric"):
        decide(dataset, [row], [bad], contract)
    bad = deepcopy(row)
    bad["tool_message"]["evidence"][0]["text"] = "xyz"
    with pytest.raises(ValueError, match="source provenance"):
        decide(dataset, [row], [bad], contract)
    bad = deepcopy(row)
    bad["fused"] = ["changed"]
    with pytest.raises(ValueError, match="candidate/query drift"):
        decide(dataset, [row], [bad], contract)
