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


@pytest.mark.parametrize('termination,scope', [('user_stop','official_checks'), ('max_steps','termination_gate_only')])
def test_scoring_exposes_termination_gate_without_relabeling_zero_as_database_failure(termination, scope):
    simulation = SimpleNamespace(termination_reason=SimpleNamespace(value=termination))
    result = asyncio.run(score_simulation(simulation, 'task',
        evaluator=lambda *a, **k: SimpleNamespace(reward=0, model_dump=lambda: {'reward': 0}),
        evaluations=tuple(Check)))
    assert result['evaluation_scope'] == scope
    assert result['env']['reward'] == 0


def test_cancelled_simulator_is_joined_before_caller_can_close_dependencies():
    from threading import Event
    from scripts.run_tau3_full import joined_thread
    entered, stop, finished = Event(), Event(), Event()
    def simulate():
        entered.set()
        assert stop.wait(2)
        finished.set()
        raise RuntimeError('stopped')
    async def run():
        task = asyncio.create_task(joined_thread(simulate, on_cancel=stop.set))
        await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
    asyncio.run(run())
