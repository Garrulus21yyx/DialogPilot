"""Production-chain evaluation must call ChatApplication, never a worker shortcut."""
import asyncio
import pytest

from application.chat_contracts import ChatCommand, Completed, StageObservation, StageStatus
from evaluation.chat_application_runner import ChatApplicationRunner
from evaluation.evaluator import EndToEndEvaluator, QualityScores


def _completed(stages=()):
    return Completed(
        response_id="response-1",
        response={
            "response": "订单正在配送中",
            "agent_type": "general",
            "intent": "order_query",
            "agent_types": ["general"],
            "routing_disposition": "execute",
            "task_plan": {
                "tasks": [{"task_id": "general_task", "owner": "general"}],
            },
            "coverage": {
                "complete": True,
                "required_task_ids": ["general_task"],
                "completed_task_ids": ["general_task"],
            },
            "agent_outcomes": [{"task_id": "general_task", "status": "success"}],
            "tool_audit": [{"tool_name": "order_lookup", "status": "success"}],
        },
        stages=tuple(stages),
    )


def test_runner_applies_all_replaceable_handles_and_captures_owner_state():
    captured = {}
    ticks = iter([10.0, 10.025])
    stage = StageObservation("delivery", StageStatus.OK, {"response_id": "response-1"})

    class Application:
        async def handle(self, command):
            captured["command"] = command
            return _completed([stage])

    def factory(overrides):
        captured["overrides"] = overrides
        return Application()

    async def delivery_probe(_command, outcome):
        return {"response_id": outcome.response_id, "persisted": True}

    runner = ChatApplicationRunner(
        factory,
        state_probes={"delivery": delivery_probe},
    ).with_overrides(
        model="model-double",
        clock=lambda: next(ticks),
        business_backend="business-double",
        knowledge_index="knowledge-double",
        delivery_adapter="delivery-double",
    )
    command = ChatCommand(message="查订单", user_id="user-1", request_id="request-1")
    result = asyncio.run(runner.run(command))

    assert captured["command"] is command
    assert captured["overrides"].model == "model-double"
    assert captured["overrides"].business_backend == "business-double"
    assert captured["overrides"].knowledge_index == "knowledge-double"
    assert captured["overrides"].delivery_adapter == "delivery-double"
    assert result.stages == (stage,)
    assert result.owner_state["delivery"]["persisted"] is True
    assert result.latency_ms == pytest.approx(25.0)


def test_full_execution_uses_chat_runner_and_records_typed_stages():
    class MustNotRunOrchestrator:
        async def run(self, _request):
            raise AssertionError("full execution must not call orchestrator.run directly")

    class Judge:
        async def judge(self, *_args, **_kwargs):
            return QualityScores(1.0, 1.0, 1.0, 1.0)

    stage = StageObservation("memory_write", StageStatus.OK, {"message_count": 2})

    class Application:
        async def handle(self, _command):
            return _completed([stage])

    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._orchestrator = MustNotRunOrchestrator()
    evaluator._chat_runner = ChatApplicationRunner(lambda _overrides: Application())
    evaluator._judge = Judge()

    results = asyncio.run(evaluator._evaluate_dialog_case({
        "id": "production-chain",
        "question": "查订单",
        "expected_agents": ["general"],
        "expected_task_ids": ["general_task"],
    }, 0))

    assert results[0].passed is True
    assert results[0].metadata["chat_outcome"] == "Completed"
    assert results[0].metadata["chat_stages"] == [stage.to_dict()]
    assert results[0].metadata["execution_mode"] == "full_execution"


def test_full_execution_without_application_runner_fails_closed():
    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._orchestrator = object()
    evaluator._chat_runner = None
    evaluator._judge = object()

    try:
        asyncio.run(evaluator._evaluate_dialog_case({"question": "hello"}, 0))
    except RuntimeError as exc:
        assert "ChatApplicationRunner" in str(exc)
    else:
        raise AssertionError("full execution must not fall back to orchestrator.run")
