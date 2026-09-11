import numpy as np
import pytest

pytest.importorskip("sklearn", reason="optional semantic-intent runtime is not installed")

from evaluation.intent_cascade import (
    replay_cascade,
    risk_coverage_point,
    select_threshold_for_precision,
    stratified_group_folds,
)


def test_group_folds_keep_translation_with_source():
    labels = ["a", "a", "a", "a", "other", "other"]
    groups = ["source-1", "source-1", "source-2", "source-2", "oos-1", "oos-2"]
    assigned = stratified_group_folds(labels, groups, folds=2)
    assert assigned[0] == assigned[1]
    assert assigned[2] == assigned[3]
    assert set(assigned) == {0, 1}


def test_group_folds_reject_cross_label_leakage():
    with pytest.raises(ValueError, match="cross labels"):
        stratified_group_folds(["a", "b"], ["same", "same"])


def test_threshold_selects_highest_coverage_meeting_precision():
    expected = ["a", "a", "b", "b"]
    predicted = ["a", "b", "b", "b"]
    confidence = [0.9, 0.4, 0.8, 0.7]
    selected = select_threshold_for_precision(
        expected, predicted, confidence, target_accuracy=1.0
    )
    assert selected["threshold"] == 0.7
    assert selected["coverage"] == 0.75
    assert selected["accepted_accuracy"] == 1.0


def test_risk_coverage_uses_inclusive_threshold():
    point = risk_coverage_point(["a"], ["a"], [0.7], 0.7)
    assert point["accepted_count"] == 1


def test_cascade_routes_only_low_confidence_to_llm():
    result = replay_cascade(
        ["a", "b"],
        ["a", "a"],
        [0.9, 0.4],
        [{"intent": "b", "confidence": 0.8}, {"intent": "b", "confidence": 0.95}],
        threshold=0.7,
    )
    assert result["predictions"] == ["a", "b"]
    assert result["route_by_index"] == ["encoder", "llm"]
    assert result["metrics"]["accuracy"] == 1.0
