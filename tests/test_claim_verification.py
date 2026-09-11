"""Semantic assessment contract, transport failures and evidence binding."""
import asyncio
import copy
import itertools
import pytest
from core.model_policy import ModelProfile, ModelRole, ReasoningEffort
from core.provider_context_budget import ProviderContextBudgetExceeded
from services.claim_verification import make_request, assess, verify_claims
from tests.framework_structured_stub import models


def fixture():
    return make_request("问", "当前状态未知。", {"status": "unknown", "version": 1}), {
        "supported": True, "answered": True, "issues": []}


@pytest.mark.parametrize("supported,answered", itertools.product([False, True], repeat=2))
def test_independent_assessment_dimensions(supported, answered):
    request, output = fixture()
    output.update(supported=supported, answered=answered,
                  issues=[] if supported and answered else ["具体证据不足或未回答"])
    result = assess(request, output)
    assert (result.supported, result.answered) == (supported, answered)


@pytest.mark.parametrize("mutation", [
    lambda o: o.pop("supported"),
    lambda o: o.update(supported="true"),
    lambda o: o.update(answered=1),
    lambda o: o.update(approval_terms_complete=None),
    lambda o: o.update(issues=[""]),
    lambda o: o.update(issues=["issue"] * 9),
    lambda o: o.update(extra=True),
    lambda o: o.update(supported=False, issues=[]),
])
def test_unknown_or_unactionable_outputs_are_rejected(mutation):
    request, output = fixture()
    mutation(output)
    with pytest.raises(ValueError):
        assess(request, output)


def test_binding_changes_with_each_authoritative_input():
    request, output = fixture()
    result = assess(request, output)
    assert result.matches(**request)
    for field, value in [("question", "另一问题"), ("answer", "已完成"),
                         ("evidence", {"status": "unknown", "version": 2})]:
        changed = copy.deepcopy(request)
        changed[field] = value
        assert not result.matches(**changed)


@pytest.mark.parametrize("targets", [[], ["a"], ["b"], ["a", "b"]])
def test_question_evidence_is_bound_without_granting_workflow_control(targets):
    request, output = fixture()
    request["evidence"]["context"] = {"requested_inputs": [
        {"target_work_item_id": item} for item in targets]}
    result = assess(request, output)
    assert not hasattr(result, "rejected_input_work_items")
    assert not hasattr(result, "approval_terms_complete")
    changed = copy.deepcopy(request)
    changed["evidence"]["context"]["requested_inputs"].append({"target_work_item_id": "new"})
    assert not result.matches(**changed)


@pytest.mark.parametrize("rejected,answered,issues", [
    (["other"], False, ["Invalid input"]), (["a", "a"], False, ["Invalid input"]),
    (["a"], True, ["Invalid input"]), (["a"], False, []),
])
def test_invalid_input_assessment_rejects_unbound_duplicate_and_contradictory_results(rejected, answered, issues):
    request, output = fixture()
    request["evidence"]["context"] = {"requested_inputs": [{"target_work_item_id": "a"}]}
    output.update(rejected_input_work_items=rejected, answered=answered, issues=issues)
    with pytest.raises(ValueError):
        assess(request, output)


@pytest.mark.parametrize("stop,name,count", [
    ("max_tokens", "submit_claim_checks", 1), ("refusal", "submit_claim_checks", 1),
    ("tool_use", "other", 1), ("tool_use", "submit_claim_checks", 0),
    ("tool_use", "submit_claim_checks", 2),
])
def test_incomplete_or_wrong_sdk_output_is_rejected(stop, name, count):
    request, output = fixture()
    model = models(output, name=name, stop=stop, count=count)[ModelRole.INTENT]
    model.responses = model.responses * 2  # Pure verifier permits one protocol retry.
    if stop == "tool_use":
        # The verifier retries a malformed protocol result once. Supply the same
        # invalid response twice to prove the finite retry still fails closed.
        model.responses *= 2
    with pytest.raises(ValueError):
        asyncio.run(verify_claims(model, ModelProfile("test"), **request))
    assert model.calls == (2 if stop == "tool_use" else 1)


def test_budget_checked_before_network():
    request, _ = fixture()
    profile = ModelProfile("deepseek-v4-flash", ReasoningEffort.NONE, "deepseek", 0, 1024)
    with pytest.raises(ProviderContextBudgetExceeded):
        asyncio.run(verify_claims(object(), profile, **request))
