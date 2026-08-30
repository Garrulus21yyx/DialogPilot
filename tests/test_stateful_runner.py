"""Stateful fixture 必须真实执行、完整注册并默认失败。"""
import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from evaluation.dataset import DatasetBundle
from evaluation.stateful_runner import (
    StatefulExecutionError,
    execute_case,
    registered_fixtures,
    run_stateful,
)


DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "dialogpilot-500-v1"


def test_all_100_stateful_cases_have_registered_real_fixtures_and_observations():
    bundle = DatasetBundle.load(DATASET)
    cases = bundle.select(layer="stateful", gold_only=False)
    actions = {case.input["scenario"]["action"] for case in cases}

    assert len(cases) == 100
    assert actions == set(registered_fixtures())
    predictions = [asyncio.run(execute_case(case)) for case in cases]
    assert len(predictions) == 100
    assert all(prediction["evidence"] for prediction in predictions)


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
