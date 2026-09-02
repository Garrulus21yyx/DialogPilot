"""M6-T03 dual review, arbitration, privacy and fresh-heldout properties."""
import hashlib
import json
from pathlib import Path

import pytest

from evaluation.gold_review import (
    Arbitration,
    BlindAnnotation,
    GoldReviewError,
    ReviewCandidate,
    assign_group_safe_splits,
    build_review_manifest,
    cohens_kappa,
    decide_review,
    error_slice_report,
    policy_contract,
)


ROOT = Path(__file__).resolve().parents[1]


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _candidate(candidate_id="case-one", *, message="退款什么时候到账", semantic="refund"):
    return ReviewCandidate(
        candidate_id=candidate_id,
        sanitized_input={"message": message},
        source_refs=(f"ticket-ref:{_hash(candidate_id)}",),
        group_hashes={
            "user": _hash("user-one"), "order": _hash("order-one"),
            "product": _hash("product-one"), "time": _hash("week-one"),
            "semantic": _hash(semantic),
        },
        slices=("standard",),
        privacy_review_id="privacy-review-1",
    )


def _annotation(candidate_id, reviewer, label, time="2026-09-02T10:00:00Z"):
    return BlindAnnotation(
        candidate_id, reviewer, {"route_mode": label}, time,
    )


def test_dual_agreement_requires_distinct_reviewers_and_no_fake_arbitration():
    first = _annotation("case-one", "reviewer-a", "KNOWLEDGE_ONLY")
    second = _annotation("case-one", "reviewer-b", "KNOWLEDGE_ONLY")

    decision = decide_review(first, second)

    assert decision.status == "AGREED"
    assert decision.reviewer_ids == ("reviewer-a", "reviewer-b")
    with pytest.raises(GoldReviewError, match="distinct"):
        decide_review(first, _annotation("case-one", "reviewer-a", "KNOWLEDGE_ONLY"))
    with pytest.raises(GoldReviewError, match="must not fabricate"):
        decide_review(first, second, arbitration=Arbitration(
            "case-one", "reviewer-c", {"route_mode": "KNOWLEDGE_ONLY"},
            "2026-09-02T11:00:00Z", "not needed",
        ))


def test_disagreement_requires_independent_auditable_arbitration():
    first = _annotation("case-one", "reviewer-a", "KNOWLEDGE_ONLY")
    second = _annotation("case-one", "reviewer-b", "TOOL_ONLY")
    with pytest.raises(GoldReviewError, match="requires arbitration"):
        decide_review(first, second)

    decision = decide_review(first, second, arbitration=Arbitration(
        "case-one", "reviewer-c", {"route_mode": "HYBRID"},
        "2026-09-02T11:00:00Z", "policy and live status are both required",
    ))
    assert decision.status == "ARBITRATED"
    assert decision.arbitration.arbitrator_id == "reviewer-c"


def test_direct_contact_identifier_is_rejected_before_annotation():
    with pytest.raises(GoldReviewError, match="direct contact"):
        _candidate(message="联系 me@example.com 处理退款")
    with pytest.raises(GoldReviewError, match="direct contact"):
        _candidate(message="请拨打 138 0013 8000")


def test_group_safe_split_keeps_all_five_dimension_matches_together():
    first = _candidate("case-one")
    second = _candidate("case-two")
    splits = assign_group_safe_splits(
        (first, second), heldout_percent=20, split_salt="frozen-split-v1",
    )
    assert splits["case-one"] == splits["case-two"]


def test_kappa_requires_paired_annotations_and_reports_agreement():
    left = (
        _annotation("one", "a", "A"), _annotation("two", "a", "B"),
        _annotation("three", "a", "A"), _annotation("four", "a", "B"),
    )
    right = (
        _annotation("one", "b", "A"), _annotation("two", "b", "B"),
        _annotation("three", "b", "A"), _annotation("four", "b", "B"),
    )
    assert cohens_kappa(left, right, label="route_mode") == 1.0
    with pytest.raises(GoldReviewError, match="paired"):
        cohens_kappa(left, right[:-1], label="route_mode")


def test_consumed_fix_case_is_removed_from_fresh_heldout_count():
    candidates = (_candidate("case-one"), _candidate("case-two", semantic="other"))
    decisions = []
    for candidate in candidates:
        decisions.append(decide_review(
            _annotation(candidate.candidate_id, "reviewer-a", "KNOWLEDGE_ONLY"),
            _annotation(candidate.candidate_id, "reviewer-b", "KNOWLEDGE_ONLY"),
        ))
    splits = {"case-one": "heldout", "case-two": "heldout"}
    decisions[0] = decisions[0].consume_for_fix("a" * 40)

    manifest = build_review_manifest(candidates, decisions, splits)

    assert manifest["case_count"] == 2
    assert manifest["fresh_heldout_count"] == 1
    assert manifest["consumed_regression_count"] == 1
    assert manifest["rows"][0]["fix_commits"] == ["a" * 40]


def test_release_report_keeps_confidence_interval_and_error_slices():
    candidates = (
        _candidate("case-one"),
        ReviewCandidate(
            **{
                **_candidate("case-two", semantic="other").__dict__,
                "slices": ("security", "compound"),
            }
        ),
    )
    report = error_slice_report(
        candidates, {"case-one": True, "case-two": False},
    )

    assert report["overall"]["rate"] == 0.5
    assert report["overall"]["confidence_method"] == "wilson-95"
    assert report["overall"]["ci95"][0] < 0.5 < report["overall"]["ci95"][1]
    assert report["by_slice"]["security"]["rate"] == 0.0
    assert report["by_slice"]["compound"]["count"] == 1


def test_frozen_gold_review_policy_is_reproducible():
    frozen = json.loads((
        ROOT / "governance/evaluation/m6-t03-gold-review-policy-v1.json"
    ).read_text(encoding="utf-8"))

    assert frozen == policy_contract()
    assert frozen["required_group_dimensions"] == [
        "user", "order", "product", "time", "semantic",
    ]
    assert frozen["gold_promotion_owner"] == (
        "Evaluation + Product/Support Ops + Privacy"
    )
