"""A failing scoring dependency is not a failed business simulation."""
import asyncio
from enum import Enum
from types import SimpleNamespace

import pytest

from scripts.run_tau3_full import score_simulation


class Check(str, Enum):
    ALL = "all"
    ENV = "env"
    ACTION = "action"


@pytest.mark.parametrize("failed", [None, *Check])
def test_independent_checks_preserve_success_and_typed_failure(failed):
    seen = []
    def evaluate(simulation, task, *, evaluation_type, **kwargs):
        assert (simulation, task) == ("saved-simulation", "fixed-task")
        assert kwargs == {"solo_mode": False, "domain": "retail", "strict_replay": True}
        seen.append(evaluation_type)
        if evaluation_type == failed:
            raise RuntimeError("judge unavailable") from ConnectionError("upstream unavailable")
        return SimpleNamespace(reward=1.0, model_dump=lambda: {"reward": 1.0})

    result = asyncio.run(score_simulation("saved-simulation", "fixed-task",
        evaluator=evaluate, evaluations=tuple(Check)))
    assert seen == list(Check)
    assert result["official_reward"] == (None if failed == Check.ALL else 1.0)
    for check in Check:
        if check == failed:
            error = result["evaluation_errors"][check.value]
            assert error["stage"] == "evaluation"
            assert error["evaluation_type"] == check.value
            assert "ConnectionError" in str(error["exception_chain"])
            assert check.value not in result
        else:
            assert result[check.value]["reward"] == 1.0
    assert len(result["evaluation_errors"]) == int(failed is not None)
