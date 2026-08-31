import json

import pytest

from evaluation.intent_weight_calibration import (
    IsotonicCalibration,
    evaluate_frozen_policy,
    fit_isotonic,
    select_calibrated_policy,
    stratified_group_folds,
)


def _cases(split="dev"):
    rows = []
    for label in ("account_security", "other"):
        for index in range(10):
            rows.append({
                "case_id": f"{label}-{index}",
                "analysis_split": split,
                "expected": label,
                "source_group_id": f"{label}-group-{index}",
                "slice": "test",
                "message": f"{label} {index}",
            })
    return rows


def _outputs(cases):
    rows = {}
    for case in cases:
        expected = case["expected"]
        wrong = "other" if expected == "account_security" else "account_security"
        rows[case["case_id"]] = {
            "llm": {"intent": expected, "confidence": 0.9},
            "ngram": {"intent": wrong, "confidence": 0.2},
            "semantic": {
                "intent": expected, "confidence": 0.8,
                "second_intent": wrong, "second_confidence": 0.5, "margin": 0.3,
            },
            "pattern": {"intent": "other", "confidence": 0.0},
            "pattern_evidence": [],
        }
    return rows


def test_isotonic_calibration_is_monotone_and_zero_is_abstention():
    model = fit_isotonic([(0.2, True), (0.4, False), (0.6, True), (0.8, True)])
    values = [model.predict(score) for score in (0.2, 0.4, 0.6, 0.8)]

    assert model.predict(0.0) == 0.0
    assert values == sorted(values)
    assert IsotonicCalibration.from_dict(model.to_dict()) == model


def test_stratified_folds_keep_groups_together_and_balance_labels():
    cases = _cases()
    cases.append({**cases[0], "case_id": "account-security-variant"})
    fold_by_id = stratified_group_folds(cases, folds=5)

    assert fold_by_id[cases[0]["case_id"]] == fold_by_id["account-security-variant"]
    for label in ("account_security", "other"):
        counts = [
            sum(case["expected"] == label and fold_by_id[case["case_id"]] == fold for case in cases)
            for fold in range(5)
        ]
        assert max(counts) - min(counts) <= 1


def test_selection_rejects_heldout_to_prevent_parameter_leakage():
    cases = _cases(split="heldout")
    with pytest.raises(ValueError, match="dev cases only"):
        select_calibrated_policy(cases, _outputs(cases))


def test_selection_freezes_calibrations_and_respects_safety_gates():
    cases = _cases()
    report = select_calibrated_policy(cases, _outputs(cases))

    assert report["status"] == "selected_on_dev_not_heldout"
    assert report["selected_oof"]["eligible"] is True
    assert report["frozen_policy"]["selection_split"] == "dev"
    assert set(report["frozen_policy"]["calibrations"]) == {
        "llm", "ngram", "semantic", "pattern"
    }


def test_frozen_evaluation_is_report_only_and_contains_paired_evidence():
    dev = _cases()
    selection = select_calibrated_policy(dev, _outputs(dev))
    heldout = _cases(split="heldout")
    report = evaluate_frozen_policy(heldout, _outputs(heldout), selection["frozen_policy"])

    assert report["status"] == "frozen_policy_report_only"
    assert report["split"] == "heldout"
    assert report["paired_accuracy_bootstrap"]["samples"] == 5000.0
    assert "exact_p_two_sided" in report["mcnemar"]
