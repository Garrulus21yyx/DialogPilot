from application.domain_encoder import DOMAINS
from evaluation.domain_calibration_audit import analyze


def scores(n, winner=1):
    row = [.001] * (len(DOMAINS) + 1)
    row[winner] = .994
    return [row] * n


def test_perfect_but_undersupported_predictions_are_not_reported_as_model_errors():
    result = analyze(scores(19), ["general"] * 19)["general"]
    assert result["argmax_rows"] == result["margin_correct"] == 19
    assert result["empirical_only_candidate"]["correct"] == 19
    assert result["diagnosis"] == "CONFIDENCE_BOUND_SAMPLE_SUPPORT_INSUFFICIENT"
    assert analyze(scores(20), ["general"] * 20)["general"]["selected"]["enabled"]


def test_bad_predictions_are_distinct_from_missing_predictions():
    report = analyze(scores(25), ["order_logistics"] * 25)
    assert report["general"]["diagnosis"] == "NO_98_PERCENT_CANDIDATE_WITH_10_SAMPLES"
    assert report["order_logistics"]["diagnosis"] == "NO_DOMAIN_WINNER"


def test_margin_rejection_is_distinct_from_model_class_absence():
    result = analyze([[.49, .5, .002, .002, .002, .002, .002]] * 25, ["general"] * 25)["general"]
    assert result["argmax_rows"] == 25 and result["margin_rows"] == 0
    assert result["diagnosis"] == "MARGIN_REJECTED_ALL"
