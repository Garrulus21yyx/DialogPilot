"""One-turn assertions over the real ``ChatApplication`` lifecycle."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from application.chat_contracts import ChatCommand
from evaluation.chat_application_runner import ChatApplicationRunner, ChatRunResult
from evaluation.command_primary_eval.contracts import (
    CheckResult,
    CostResult,
    DirectEvaluationResult,
    EvalCase,
)


class ChatApplicationE2EAdapter:
    """Run one case through ``ChatApplication.handle`` and check four outcomes."""

    version = "chat-application-e2e-adapter-v1"

    def __init__(self, runner: ChatApplicationRunner) -> None:
        self._runner = runner

    async def evaluate(self, case: EvalCase) -> DirectEvaluationResult:
        run = await self._runner.run(_command(case))
        expected = case.expected
        return DirectEvaluationResult(
            trigger=_component_check(run, expected["component_invocations"]),
            artifact=_user_outcome_check(run, expected["user_outcome"]),
            consumption=_flow_transition_check(run, expected["flow_transition"]),
            outcome=_tool_effect_check(run, expected["tool_side_effects"]),
            cost=CostResult(
                latency_ms=run.latency_ms,
                token_usage=0,
                invocation_count=1,
            ),
        )


def _command(case: EvalCase) -> ChatCommand:
    state = case.initial_state
    return ChatCommand(
        message=case.message,
        tenant_id=str(state["tenant_id"]),
        user_id=str(state["user_id"]),
        conv_id=str(state["conversation_id"]),
        request_id=str(state["request_id"]),
    )


def _component_check(
    run: ChatRunResult,
    expected: Mapping[str, Any],
) -> CheckResult:
    stages = _stages(run)
    knowledge = stages["knowledge_retrieval"]
    tool_calls = tuple(run.owner_state["tool_side_effects"]["calls"])
    actual = {
        "legacy_intent": stages["intent"].status.value.upper(),
        "semantic_router": (
            "INVOKED"
            if stages["turn_understanding"].detail["semantic_router_used"]
            else "SKIPPED"
        ),
        "knowledge_rag": "INVOKED" if knowledge.detail["used"] else "SKIPPED",
        "business_tool": "INVOKED" if tool_calls else "SKIPPED",
    }
    return _exact(expected, actual)


def _user_outcome_check(
    run: ChatRunResult,
    expected: Mapping[str, Any],
) -> CheckResult:
    response = run.public_response
    fragments = tuple(map(str, expected.get("response_contains") or ()))
    actual = {
        "outcome_type": type(run.outcome).__name__,
        "intent": response.get("intent"),
        "routing_disposition": response.get("routing_disposition"),
        "coverage_complete": bool((response.get("coverage") or {}).get("complete")),
    }
    expected_fields = {
        key: expected[key]
        for key in (
            "outcome_type",
            "intent",
            "routing_disposition",
            "coverage_complete",
        )
    }
    missing = tuple(
        fragment
        for fragment in fragments
        if fragment not in str(response.get("response") or "")
    )
    return CheckResult(
        actual == expected_fields and not missing,
        {"expected": expected_fields, "actual": actual, "missing_fragments": missing},
    )


def _flow_transition_check(
    run: ChatRunResult,
    expected: Mapping[str, Any],
) -> CheckResult:
    stage = _stages(run)["flow_transition"]
    actual = {
        "stage_status": stage.status.value,
        "stage_detail": dict(stage.detail),
        "owner_state": dict(run.owner_state["flow_state"]),
    }
    return _exact(expected, actual)


def _tool_effect_check(
    run: ChatRunResult,
    expected: Mapping[str, Any],
) -> CheckResult:
    calls = tuple(run.owner_state["tool_side_effects"]["calls"])
    actual = {"calls": calls}
    normalized_expected = {
        "calls": tuple(dict(item) for item in expected.get("calls") or ()),
    }
    return _exact(normalized_expected, actual)


def _stages(run: ChatRunResult) -> dict[str, Any]:
    return {stage.stage: stage for stage in run.stages}


def _exact(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> CheckResult:
    return CheckResult(
        dict(expected) == dict(actual),
        {"expected": dict(expected), "actual": dict(actual)},
    )
