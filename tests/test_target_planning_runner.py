import asyncio
import subprocess
import sys

from application.chat_contracts import ChatCommand
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.turn_planning import (
    CommandKind, CommandProposal, ProposalDisposition, TurnProposal,
)
from application.work_item import ArgumentValue
from evaluation.evaluator import EndToEndEvaluator
from evaluation.target_planning_runner import TargetPlanningRunner


def test_planning_uses_real_target_compilation_with_isolated_state_and_context():
    captured = []

    async def understanding(observations, state, deterministic, registry, turn_context):
        captured.append((observations, state, turn_context))
        return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
            "lookup-order", CommandKind.DIRECT_TOOL, "order_logistics", "查询订单",
            (ArgumentValue.create("order_id", "DP1234"),), ("order.current_state",),
            tool_id="order_lookup",
        ),), "EVAL_PLAN")

    async def must_not_execute(_context):
        raise AssertionError("planning evaluation cannot execute tools")

    registry = build_default_capability_registry("default")
    runtime = OrchestrationRuntime(direct_executor=must_not_execute, domain_workers={})
    runner = TargetPlanningRunner(
        registry=registry, understanding=understanding, orchestration=runtime,
    )
    plan = asyncio.run(runner.plan(
        ChatCommand("它到哪了", "eval-user", conv_id="eval-dialog", request_id="eval-request"),
        history=[{"role": "user", "content": "订单是 DP1234"}],
        entities={"order_id": "DP1234"},
    ))
    assert plan.route.owner_ids == ("order_logistics",)
    assert plan.registry_fingerprint == registry.fingerprint
    assert plan.work.items[0].allowed_tools == ("order_lookup",)
    assert captured[0][0].structured_fields == (("order_id", "DP1234"),)
    assert captured[0][2].recent_messages[0].content == "订单是 DP1234"
    assert runtime.get_stats() == {}


def test_legacy_intent_labels_do_not_override_target_planning_inputs():
    inputs = []

    class Planner:
        async def plan(self, command, *, history, entities):
            inputs.append((command, history, entities))
            from application.capability_registry import CapabilityRisk
            from application.turn_planning import RouteDecision, RouteMode, TurnPlan
            return TurnPlan(
                RouteDecision(RouteMode.CLARIFY, (), (), CapabilityRisk.LOW, (), "TEST"),
                None, None, "state", "registry", "compiler",
            )

    evaluator = EndToEndEvaluator.__new__(EndToEndEvaluator)
    evaluator._planning_runner_factory = Planner
    case = {
        "question": "查一下", "evaluation_layer": "routing", "entities": {"order_id": "DP1234"},
        "expected_agents": [], "expected_task_ids": [],
    }
    from core.intent_recognizer import IntentCategory
    for intent in (*[item.value for item in IntentCategory], "future_analytics_label"):
        asyncio.run(evaluator._evaluate_dialog_case({**case, "intent": intent}, 0))
    assert all(item == inputs[0] for item in inputs)


def test_api_and_evaluation_import_without_legacy_execution_modules():
    subprocess.run([sys.executable, "-c", """
import sys
import api.main
import evaluation.evaluator
assert not ({'agents.agent_orchestrator', 'agents.react_engine', 'agents.run_store',
             'application.chat_application'} & sys.modules.keys())
"""], check=True)
