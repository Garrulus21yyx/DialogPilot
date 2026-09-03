"""Adapter from JSON eval cases to the selective command producer."""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from application.route_policy_v2 import FlowActionRegistry
from application.selective_command_producer import (
    CommandProducerStage,
    SelectiveCommandProducer,
)
from application.turn_state import TurnStateSnapshot
from application.turn_understanding import (
    CommandProposal,
    UnderstandingStatus,
    fingerprint_message,
)
from evaluation.command_primary_eval.contracts import EvalCase
from evaluation.command_primary_eval.understanding import (
    CheckResult,
    CostResult,
    UnderstandingDirectResult,
)


class SelectiveUnderstandingAdapter:
    """Evaluate one state-aware case at the Understanding boundary."""

    version = "selective-command-producer-adapter-v1"

    def __init__(
        self,
        producer: SelectiveCommandProducer,
        state_loader: Callable[[EvalCase], TurnStateSnapshot],
        registry_loader: Callable[[TurnStateSnapshot], FlowActionRegistry],
    ) -> None:
        self._producer = producer
        self._state_loader = state_loader
        self._registry_loader = registry_loader

    async def evaluate(self, case: EvalCase) -> UnderstandingDirectResult:
        state = self._state_loader(case)
        registry = self._registry_loader(state)
        history = tuple(
            dict(item) for item in case.initial_state.get("history", ())
        )
        outcome = await self._producer.produce(
            case.message,
            fingerprint_message(case.message),
            state,
            registry,
            history=history,
        )

        expected_stage = str(case.expected.get("producer_stage") or "")
        trigger_passed = not expected_stage or outcome.stage.value == expected_stage
        expected_commands = tuple(
            _normalize_expected_command(item)
            for item in case.expected.get("commands", ())
        )
        actual_commands = tuple(
            _command_artifact(command)
            for command in outcome.understanding.commands
        )
        command_passed = actual_commands == expected_commands

        expected_candidates = tuple(
            _normalize_expected_candidate(item)
            for item in case.expected.get("encoder_candidates", ())
        )
        actual_candidates = tuple(
            _candidate_artifact(item)
            for item in outcome.encoder_decision.candidates
        )
        consumption_passed = (
            not expected_candidates
            or all(item in actual_candidates for item in expected_candidates)
        )

        expected_status = str(
            case.expected.get("understanding_status")
            or UnderstandingStatus.RESOLVED.value
        )
        status_passed = outcome.understanding.status.value == expected_status
        return UnderstandingDirectResult(
            trigger=CheckResult(trigger_passed, {
                "expected_stage": expected_stage or None,
                "actual_stage": outcome.stage.value,
                "encoder_disposition": outcome.encoder_decision.disposition.value,
            }),
            artifact=CheckResult(command_passed, {
                "expected_commands": expected_commands,
                "actual_commands": actual_commands,
            }),
            consumption=CheckResult(consumption_passed, {
                "expected_encoder_candidates": expected_candidates,
                "actual_encoder_candidates": actual_candidates,
            }),
            outcome=CheckResult(status_passed, {
                "expected_status": expected_status,
                "actual_status": outcome.understanding.status.value,
            }),
            cost=CostResult(
                latency_ms=outcome.latency_ms,
                token_usage=0,
                invocation_count=(
                    1 if outcome.stage is CommandProducerStage.ENCODER else 2
                ),
            ),
        )

def _command_artifact(command: CommandProposal) -> Mapping[str, Any]:
    flow = command.target_flow or (
        command.source_flow.definition if command.source_flow else None
    )
    return {
        "kind": command.kind.value,
        "flow_id": flow.flow_id if flow else None,
        "flow_version": flow.version if flow else None,
        "arguments": {
            item.name: json.loads(item.value_json) for item in command.arguments
        },
    }


def _candidate_artifact(candidate: Any) -> Mapping[str, Any]:
    return {
        "kind": candidate.command_kind.value,
        "flow_id": candidate.flow.flow_id if candidate.flow else None,
        "flow_version": candidate.flow.version if candidate.flow else None,
    }


def _normalize_expected_command(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "kind": str(value["kind"]),
        "flow_id": value.get("flow_id"),
        "flow_version": value.get("flow_version"),
        "arguments": dict(value.get("arguments") or {}),
    }


def _normalize_expected_candidate(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "kind": str(value["kind"]),
        "flow_id": value.get("flow_id"),
        "flow_version": value.get("flow_version"),
    }
