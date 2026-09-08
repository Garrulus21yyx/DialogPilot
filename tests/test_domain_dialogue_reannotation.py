import pytest
from itertools import combinations

from evaluation.domain_dialogue_reannotation import reannotated_rows, DomainSetReview, DomainSetVerdict
from evaluation.domain_dialogue_teacher import ReviewedBatch, Verdict
from application.domain_encoder import DOMAINS


def test_all_supported_domain_sets_have_one_deterministic_label():
    for size in range(1, len(DOMAINS) + 1):
        for domains in combinations(DOMAINS, size):
            for ordered in (list(domains), list(reversed(domains)), list(domains) * 2):
                verdict = DomainSetVerdict.model_construct(case_id="x", active_domains=ordered,
                                                          clear_and_natural=True, reason="audit")
                result = DomainSetReview(verdicts=[verdict]).labels().verdicts[0].label
                assert result == (domains[0] if size == 1 else "__DEFER__")


@pytest.mark.parametrize("label", ["general", "product_technical", "order_logistics",
    "billing_refund", "account_security", "human_service", "__DEFER__"])
def test_new_review_owns_label_and_preserves_prefix(label):
    cases = [{"case_id": "domain-v3:zh:1:2:0", "text": "current", "messages": []}]
    review = ReviewedBatch(verdicts=[Verdict(case_id=cases[0]["case_id"], label=label,
                                           clear_and_natural=True, reason="review")])
    rows = reannotated_rows(cases, review, "zh")
    assert rows[0]["label"] == label and rows[0]["text"] == "current"
    assert rows[0]["group_id"] == "domain-v3:zh:1:2"
    assert cases == [{"case_id": "domain-v3:zh:1:2:0", "text": "current", "messages": []}]
    review.verdicts[0].clear_and_natural = False
    assert reannotated_rows(cases, review, "zh") == []


def test_incomplete_or_duplicate_review_is_not_accepted():
    cases = [{"case_id": "x:0", "text": "x", "messages": []}]
    verdict = Verdict(case_id="x:0", label="general", clear_and_natural=True, reason="review")
    for verdicts in ([], [verdict, verdict], [verdict.model_copy(update={"case_id": "other"})]):
        with pytest.raises(ValueError, match="coverage"):
            reannotated_rows(cases, ReviewedBatch(verdicts=verdicts), "zh")
