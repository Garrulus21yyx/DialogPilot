import pytest

from evaluation.domain_trial_metrics import summarize


def test_collapsed_classifier_is_not_successful_selective_routing():
    result = summarize([{"expected": label, "predicted": "refund"}
                        for label in ("refund", "order", "general")],
                       ["refund", "order", "general"])
    assert result["correct"] == 1
    assert result["accuracy"] == pytest.approx(1 / 3)
    assert result["macro_f1"] == pytest.approx(1 / 6)
    assert result["predicted_counts"] == {"refund": 3}
