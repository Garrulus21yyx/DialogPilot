"""Stateful fixture 必须真实执行、完整注册并默认失败。"""
import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from evaluation.dataset import DatasetBundle
from evaluation.stateful_runner import (
    FixtureEvidence,
    FixtureRequest,
    StatefulExecutionError,
    execute_case,
    registered_fixtures,
    run_stateful,
)
from memory.context import ContextAssembler
from memory.conversation_memory import MemoryManager


DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dialogpilot-500-v1"
FRESH_DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dialogpilot-stateful-fresh-v2"


@pytest.mark.parametrize("outcomes,pending,rejected,complete", [
    ([], ["a", "b"], None, False),
    (["b"], ["a"], None, False),
    (["b", "a"], [], None, True),
    (["a", "a"], ["b"], "a", False),
    (["a", "b", "outside"], [], "outside", True),
])
def test_coverage_probe_exposes_current_owner_and_distinguishes_rejection_from_completion(
    outcomes, pending, rejected, complete,
):
    from evaluation.result_board_fixture import observe_result_submissions

    observed = observe_result_submissions(["a", "b"], outcomes)
    assert observed["owner"] == "application.result_board.ResultBoard.evaluate"
    assert observed["pending_work_item_ids"] == pending
    assert observed["accepted_prefix_complete"] is complete
    assert observed["submission_rejected"] is (rejected is not None)
    if rejected is not None:
        assert observed["rejection"]["work_item_id"] == rejected
        assert observed["rejection"]["error"]
    assert observed["successful_result_ids"] == observed["accepted_result_ids"]


def test_loop_budget_fixture_runs_target_framework_with_paired_call_ids():
    from evaluation.stateful_runner import _react_max_steps

    evidence = asyncio.run(_react_max_steps(FixtureRequest("loop", {}, "lookup")))
    assert all(evidence.assertions.values())
    assert evidence.details["steps"] == 2
    assert evidence.details["call_ids"] == ["loop-1", "loop-2"]


def test_all_100_stateful_cases_have_registered_real_fixtures_and_observations():
    bundle = DatasetBundle.load(DATASET)
    cases = bundle.select(layer="stateful", gold_only=False)
    actions = {case.input["scenario"]["action"] for case in cases}

    assert len(cases) == 100
    assert actions <= set(registered_fixtures())
    predictions = [asyncio.run(execute_case(case)) for case in cases]
    assert len(predictions) == 100
    assert all(prediction["evidence"] for prediction in predictions)


def test_all_fresh_reviewer_b_actions_are_registered_and_execute():
    bundle = DatasetBundle.load(FRESH_DATASET)
    cases = bundle.select(layer="stateful", split="heldout", gold_only=False)
    actions = {case.input["scenario"]["action"] for case in cases}

    assert len(cases) == 27
    assert actions <= set(registered_fixtures())
    predictions, report = asyncio.run(run_stateful(bundle, split="heldout"))
    assert len(predictions) == 27
    assert report["pass_rate"] == 1.0


@pytest.mark.parametrize("split,count", [("dev", 80), ("heldout", 20)])
def test_stateful_split_runs_through_deterministic_scorer(split, count):
    predictions, report = asyncio.run(run_stateful(DatasetBundle.load(DATASET), split=split))

    assert len(predictions) == count
    assert report["case_count"] == count
    assert report["pass_rate"] == 1.0
    assert report["layers"]["stateful"] == {
        "all_assertions_pass": 1.0,
        "assertion_pass_rate": 1.0,
    }


def test_unknown_fixture_fails_closed():
    case = DatasetBundle.load(DATASET).select(layer="stateful")[0]
    scenario = dict(case.input["scenario"])
    scenario["action"] = "invented_fixture"
    changed = replace(case, input={**case.input, "scenario": scenario})

    with pytest.raises(StatefulExecutionError, match="unregistered fixture"):
        asyncio.run(execute_case(changed))


def test_unobserved_assertion_cannot_be_copied_from_expected():
    case = DatasetBundle.load(DATASET).select(layer="stateful")[0]
    changed = replace(case, expected={"assertions": {"invented_truth": True}})

    with pytest.raises(StatefulExecutionError, match="did not observe assertions"):
        asyncio.run(execute_case(changed))


def test_fixture_request_cannot_read_expected_or_mutate_scenario(monkeypatch):
    """Expected truth 不得跨入 actual 生产路径。"""
    import evaluation.stateful_runner as runner

    case = _case("stateful-memory-context-budget-1")
    scenario = dict(case.input["scenario"])
    scenario["action"] = "expected_copy_attack"
    changed = replace(case, input={**case.input, "scenario": scenario})

    async def forged(request: FixtureRequest) -> FixtureEvidence:
        assert not hasattr(request, "expected")
        with pytest.raises(TypeError):
            request.scenario["setup"] = "forged"
        return FixtureEvidence(
            assertions=request.expected["assertions"],  # type: ignore[attr-defined]
            details={},
        )

    monkeypatch.setitem(runner._FIXTURES, "expected_copy_attack", forged)
    with pytest.raises(StatefulExecutionError, match="AttributeError"):
        asyncio.run(execute_case(changed))


def _case(case_id: str):
    return next(
        case
        for case in DatasetBundle.load(DATASET).select(layer="stateful")
        if case.case_id == case_id
    )


@pytest.mark.parametrize(
    "case_id",
    ["stateful-memory-empty-query-1", "stateful-memory-empty-query-2"],
)
def test_empty_query_fixture_cannot_bypass_current_context_owner(monkeypatch, case_id):
    async def fail_owner(*_args, **_kwargs):
        raise RuntimeError("search owner bypass mutation")

    monkeypatch.setattr(MemoryManager, "get_context", fail_owner)
    with pytest.raises(StatefulExecutionError, match="search owner bypass mutation"):
        asyncio.run(execute_case(_case(case_id)))


@pytest.mark.parametrize(
    "case_id",
    ["stateful-memory-summary-fallback-1", "stateful-memory-summary-fallback-2"],
)
def test_summary_fallback_fixture_cannot_bypass_fallback_owner(monkeypatch, case_id):
    def fail_owner(*_args, **_kwargs):
        raise RuntimeError("fallback owner bypass mutation")

    monkeypatch.setattr(MemoryManager, "_fallback_summary", fail_owner)
    with pytest.raises(StatefulExecutionError, match="fallback owner bypass mutation"):
        asyncio.run(execute_case(_case(case_id)))


@pytest.mark.parametrize(
    "case_id",
    ["stateful-memory-context-escape-1", "stateful-memory-context-escape-2"],
)
def test_context_quarantine_fixture_cannot_bypass_assembler_owner(monkeypatch, case_id):
    def fail_owner(*_args, **_kwargs):
        raise RuntimeError("context owner bypass mutation")

    monkeypatch.setattr(ContextAssembler, "assemble", fail_owner)
    with pytest.raises(StatefulExecutionError, match="context owner bypass mutation"):
        asyncio.run(execute_case(_case(case_id)))


@pytest.mark.parametrize(
    "case_id",
    ["stateful-memory-explicit-close-1", "stateful-memory-explicit-close-2"],
)
def test_explicit_close_fixture_cannot_bypass_finalize_owner(monkeypatch, case_id):
    async def fail_owner(*_args, **_kwargs):
        raise RuntimeError("finalize owner bypass mutation")

    monkeypatch.setattr(MemoryManager, "finalize_conversation", fail_owner)
    with pytest.raises(StatefulExecutionError, match="finalize owner bypass mutation"):
        asyncio.run(execute_case(_case(case_id)))


def test_empty_query_and_empty_corpus_record_real_storage_behavior():
    empty_query = asyncio.run(execute_case(_case("stateful-memory-empty-query-1")))
    empty_corpus = asyncio.run(execute_case(_case("stateful-memory-empty-corpus-1")))

    assert empty_query["evidence"]["storage_calls"] == 0
    assert empty_query["actual"]["assertions"]["no_storage_query"] is True
    assert empty_corpus["evidence"]["storage_calls"] == 0
    assert empty_corpus["actual"]["assertions"]["result_empty"] is True
