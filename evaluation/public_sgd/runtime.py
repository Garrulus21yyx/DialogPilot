"""Production-contract materializers for the frozen SGD command benchmark."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.route_policy_v2 import (
    ActionDefinition,
    ArgumentDefinition,
    ApprovalPolicy,
    FlowActionRegistry,
    FlowDefinition,
    RoutePolicy,
    RoutePolicyError,
    WorkKind,
)
from application.turn_state import (
    ActiveFlowRef,
    FlowAggregateVersion,
    FlowBinding,
    FlowDefinitionRef,
    PrincipalScope,
    StateAvailability,
    StateSourceStatus,
    TurnStateSnapshot,
)
from application.turn_understanding import (
    CommandKind,
    UnderstandingResult,
    UnderstandingStatus,
)
from evaluation.public_sgd.adapter import FLOW_VERSION, flow_instance_id
from evaluation.public_sgd.contracts import SgdAdapterError, load_json, load_jsonl
from evaluation.public_sgd.validation import validate_frozen_benchmark


BENCHMARK_TENANT = "tenant-public-sgd-command-v1"
STATE_PRODUCER_VERSION = "sgd-command-state-materializer-v1"
CAPTURED_AT = datetime(2020, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class SgdBenchmarkDataset:
    root: Path
    split: str
    cases: tuple[Mapping[str, Any], ...]
    conversations: Mapping[str, Mapping[str, Any]]
    registry: FlowActionRegistry

    @classmethod
    def load(cls, root: Path, split: str) -> "SgdBenchmarkDataset":
        validation = validate_frozen_benchmark(root)
        if split not in validation["splits"]:
            raise SgdAdapterError(f"benchmark split is unavailable: {split}")
        cases = load_jsonl(root / split / "cases.jsonl")
        conversations = load_jsonl(root / split / "conversations.jsonl")
        by_dialogue = {str(row["dialogue_id"]): row for row in conversations}
        return cls(
            root=root,
            split=split,
            cases=cases,
            conversations=by_dialogue,
            registry=build_runtime_registry(root / "registry.json"),
        )

    def materialize(
        self, case: Mapping[str, Any]
    ) -> tuple[str, TurnStateSnapshot, tuple[Mapping[str, str], ...]]:
        input_value = case["input"]
        history_ref = input_value["history"]
        dialogue_id = str(history_ref["dialogue_id"])
        conversation = self.conversations.get(dialogue_id)
        if conversation is None:
            raise SgdAdapterError("case conversation is unavailable")
        start = int(history_ref["inclusive_start_turn"])
        end = int(history_ref["exclusive_end_turn"])
        history = tuple(
            {
                "role": "user" if turn["speaker"] == "USER" else "assistant",
                "content": str(turn["utterance"]),
            }
            for turn in conversation["turns"][start:end]
        )
        state = materialize_state(case)
        return str(input_value["message"]), state, history


def build_runtime_registry(path: Path) -> FlowActionRegistry:
    value = load_json(path)
    flows: list[FlowDefinition] = []
    actions: list[ActionDefinition] = []
    for item in value.get("flows") or []:
        ref = FlowDefinitionRef(str(item["flow_id"]), str(item["version"]))
        allowed = tuple(CommandKind(command) for command in item["allowed_commands"])
        flows.append(FlowDefinition(ref, allowed))
        transactional = bool(item["transactional"])
        for kind in allowed:
            actions.append(ActionDefinition(
                action_id=f"sgd.{kind.value.lower()}.{item['service']}.{item['intent']}",
                version="v1",
                command_kind=kind,
                flow=ref,
                work_kind=WorkKind.AGENT,
                owner=AgentType.GENERAL,
                effect=(
                    TaskEffect.WRITE_REQUIRES_APPROVAL
                    if transactional else TaskEffect.READ_ONLY
                ),
                risk=TaskRisk.HIGH if transactional else TaskRisk.LOW,
                requirement_ids=("sgd.service_call",),
                allowed_tools=(str(item["tool"]),),
                approval=(
                    ApprovalPolicy.EXPLICIT_CONFIRMATION_REQUIRED
                    if transactional else ApprovalPolicy.USER_COMMAND_SUFFICIENT
                ),
                objective=str(item["description"]),
                required_arguments=tuple(item["required_arguments"]),
                optional_arguments=tuple(item["optional_arguments"]),
                argument_definitions=tuple(
                    ArgumentDefinition(
                        name=str(argument["name"]),
                        description=str(argument["description"]),
                        possible_values=tuple(argument["possible_values"]),
                    )
                    for argument in item.get("argument_definitions") or ()
                ),
            ))
    if not flows:
        raise SgdAdapterError("benchmark registry contains no flows")
    return FlowActionRegistry(
        tenant_id=BENCHMARK_TENANT,
        generation=str(value["generation"]),
        flows=tuple(flows),
        actions=tuple(actions),
    )


def materialize_state(case: Mapping[str, Any]) -> TurnStateSnapshot:
    dialogue_id = str(case["input"]["history"]["dialogue_id"])
    principal = PrincipalScope(
        BENCHMARK_TENANT,
        f"sgd-user-{dialogue_id}",
        f"sgd-conversation-{dialogue_id}",
    )
    raw_flows = case["input"]["current_state"]["active_flows"]
    active = tuple(
        ActiveFlowRef(
            definition=FlowDefinitionRef(
                str(item["flow_id"]), str(item["version"])
            ),
            instance_id=str(item["instance_id"]),
            state_version=int(item["state_version"]),
            principal_fingerprint=principal.fingerprint,
            bindings=tuple(
                FlowBinding.create(name, value)
                for name, value in sorted((item.get("bindings") or {}).items())
            ),
        )
        for item in raw_flows
    )
    aggregate_version = max((item.state_version for item in active), default=0)
    return TurnStateSnapshot(
        request_id=str(case["case_id"]),
        principal=principal,
        flow_aggregate=(
            FlowAggregateVersion(
                f"sgd-flow-state:{dialogue_id}", aggregate_version
            )
            if active else None
        ),
        active_flows=active,
        pending_slot=None,
        recent_turn_refs=(),
        active_case_refs=(),
        reusable_media_refs=(),
        sources=(StateSourceStatus(
            "sgd_adapted_state",
            StateAvailability.CURRENT,
            STATE_PRODUCER_VERSION,
        ),),
        captured_at=CAPTURED_AT,
        producer_version=STATE_PRODUCER_VERSION,
    )


def prediction_from_understanding(
    case: Mapping[str, Any],
    understanding: UnderstandingResult,
    state: TurnStateSnapshot,
    registry: FlowActionRegistry,
) -> Mapping[str, Any]:
    try:
        accepted = RoutePolicy().accept(understanding, state, registry)
    except RoutePolicyError as exc:
        return {
            "case_id": case["case_id"],
            "status": UnderstandingStatus.INVALID_PROVIDER_OUTPUT.value,
            "commands": [],
            "next_state": case["input"]["current_state"],
            "reason_code": "ROUTE_POLICY_REJECTED",
            "error": str(exc),
        }
    del accepted
    commands = [_serialize_command(item) for item in understanding.commands]
    return {
        "case_id": case["case_id"],
        "status": understanding.status.value,
        "commands": commands,
        "next_state": _project_next_state(case, understanding),
        "reason_code": understanding.reason_code,
    }


def _serialize_command(proposal) -> Mapping[str, Any]:
    source = proposal.source_flow
    target = proposal.target_flow
    flow = target or (source.definition if source is not None else None)
    arguments = {
        item.name: item.value
        for item in (source.bindings if source is not None else ())
    }
    arguments.update({item.name: item.value for item in proposal.arguments})
    return {
        "kind": proposal.kind.value,
        "flow": {
            "flow_id": flow.flow_id if flow else None,
            "version": flow.version if flow else None,
            "instance_id": source.instance_id if source else None,
        },
        # SGD service calls contain the effective argument snapshot. Project a
        # continuation delta over its source state before comparing to that
        # public annotation.
        "arguments": arguments,
    }


def _project_next_state(
    case: Mapping[str, Any], understanding: UnderstandingResult
) -> Mapping[str, Any]:
    current = case["input"]["current_state"]
    if understanding.status is not UnderstandingStatus.RESOLVED:
        return current
    if len(understanding.commands) != 1:
        return current
    command = understanding.commands[0]
    arguments = {
        item.name: item.value
        for item in (
            command.source_flow.bindings
            if command.source_flow is not None else ()
        )
    }
    arguments.update({item.name: item.value for item in command.arguments})
    dialogue_id = str(case["input"]["history"]["dialogue_id"])
    if command.kind is CommandKind.START_FLOW and command.target_flow is not None:
        flow_id = command.target_flow.flow_id
        prefix = "sgd."
        if not flow_id.startswith(prefix) or flow_id.count(".") < 2:
            return current
        service, intent = flow_id[len(prefix):].split(".", 1)
        return {
            "active_flows": [{
                "flow_id": flow_id,
                "version": command.target_flow.version,
                "instance_id": flow_instance_id(dialogue_id, service, intent),
                "state_version": 1,
                "bindings": arguments,
            }],
            "pending_required_slots": [],
        }
    if command.kind is CommandKind.CONTINUE_FLOW and command.source_flow is not None:
        active = command.source_flow
        return {
            "active_flows": [{
                "flow_id": active.definition.flow_id,
                "version": active.definition.version,
                "instance_id": active.instance_id,
                "state_version": active.state_version + 1,
                "bindings": arguments,
            }],
            "pending_required_slots": [],
        }
    return current
