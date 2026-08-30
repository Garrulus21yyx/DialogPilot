"""RAG 消融的选型边界测试。"""
from pathlib import Path

from evaluation import retrieval_ablation


def test_ablation_selects_by_recall_then_mrr_then_ndcg_on_dev(monkeypatch):
    bundle = type("Bundle", (), {
        "manifest": {"dataset_id": "test", "cases_sha256": "abc"},
    })()
    monkeypatch.setattr(retrieval_ablation.DatasetBundle, "load", lambda _path: bundle)

    def fake_run(_bundle, *, vector_weight, lexical_weight, **_kwargs):
        recall = lexical_weight
        report = {
            "pass_rate": recall,
            "layers": {"retrieval": {
                "recall_at_5": recall,
                "mrr": lexical_weight,
                "ndcg_at_5": vector_weight,
            }},
        }
        return [], report

    monkeypatch.setattr(retrieval_ablation, "run_retrieval", fake_run)

    result = retrieval_ablation.run_ablation(Path("unused"), split="dev")

    assert result["selection_allowed"] is True
    assert result["recommended"] == "bm25_only"
    assert result["results"][0]["recall_at_5"] == 1.0


def test_consumed_heldout_is_report_only_not_a_selection_source(monkeypatch):
    bundle = type("Bundle", (), {
        "manifest": {"dataset_id": "test", "cases_sha256": "abc"},
    })()
    monkeypatch.setattr(retrieval_ablation.DatasetBundle, "load", lambda _path: bundle)
    monkeypatch.setattr(
        retrieval_ablation,
        "run_retrieval",
        lambda *_args, **_kwargs: ([], {
            "pass_rate": 1.0,
            "layers": {"retrieval": {"recall_at_5": 1.0, "mrr": 1.0, "ndcg_at_5": 1.0}},
        }),
    )

    result = retrieval_ablation.run_ablation(Path("unused"), split="heldout")

    assert result["selection_allowed"] is False
    assert result["recommended"] is None
