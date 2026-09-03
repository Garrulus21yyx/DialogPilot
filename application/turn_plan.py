"""Deterministic compilation from accepted commands to one executable turn plan."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from agents.orchestration_contracts import TaskGraph, TaskRisk, TaskSpec
from application.route_decision import RouteMode, RouteRisk
from application.route_policy_v2 import (
    AcceptedCommand,
    ApprovalPolicy,
    FlowActionRegistry,
    RoutePolicyResult,
    RoutePolicyStatus,
    WorkKind,
)
from application.turn_state import FlowDefinitionRef, TurnStateSnapshot
from application.turn_understanding import CommandArgument, CommandKind


class TurnPlanError(ValueError):
    pass


class FlowMutationKind(str, Enum):
    START = "START"
    ADVANCE = "ADVANCE"
    FILL_SLOT = "FILL_SLOT"
    PAUSE = "PAUSE"
    EXPAND = "EXPAND"
    SWITCH = "SWITCH"
    COMPLETE = "COMPLETE"
    CANCEL = "CANCEL"
    INTERRUPT = "INTERRUPT"


@dataclass(frozen=True)
class SignalConsumption:
    signal_id: str
    expected_version: int


@dataclass(frozen=True)
class SlotUpdate:
    field_name: str
    value_json: str


@dataclass(frozen=True)
class FlowMutation:
    kind: FlowMutationKind
    command_id: str
    source_instance_id: str | None
    expected_source_version: int | None
    target_flow: FlowDefinitionRef | None
    slot_update: SlotUpdate | None
    signal_consumption: SignalConsumption | None


@dataclass(frozen=True)
class FlowTransitionPlan:
    aggregate_id: str
    expected_aggregate_version: int
    mutations: tuple[FlowMutation, ...]


@dataclass(frozen=True)
class CompiledWorkItem:
    task_id: str
    source_command_id: str
    action_ref: str
    allowed_tools: tuple[str, ...]
    approval: ApprovalPolicy
    arguments: tuple[CommandArgument, ...]


@dataclass(frozen=True)
class WorkPlan:
    graph: TaskGraph
    items: tuple[CompiledWorkItem, ...]


@dataclass(frozen=True)
class RouteDecisionV2:
    mode: RouteMode
    owner_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    flow_refs: tuple[FlowDefinitionRef, ...]
    risk: RouteRisk
    reason_code: str
    policy_version: str


@dataclass(frozen=True)
class TurnPlan:
    route: RouteDecisionV2
    transitions: FlowTransitionPlan | None
    work: WorkPlan | None
    command_ids: tuple[str, ...]
    state_fingerprint: str
    registry_fingerprint: str
    compiler_version: str

    @property
    def plan_id(self) -> str:
        return "turn-plan:v1:" + _fingerprint({
            "route": {
                "mode": self.route.mode.value,
                "owners": self.route.owner_ids,
                "requirements": self.route.requirement_ids,
                "flows": [item.key for item in self.route.flow_refs],
                "risk": self.route.risk.value,
            },
            "mutations": [item.command_id for item in self.transitions.mutations]
            if self.transitions else [],
            "work": [item.source_command_id for item in self.work.items]
            if self.work else [],
            "commands": self.command_ids,
            "state": self.state_fingerprint,
            "registry": self.registry_fingerprint,
            "compiler": self.compiler_version,
        })


class TurnPlanCompiler:
    def __init__(self, compiler_version: str = "turn-plan-compiler-v1") -> None:
        self.compiler_version = compiler_version

    def compile(
        self,
        accepted: RoutePolicyResult,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> TurnPlan:
        if accepted.state_fingerprint != state.fingerprint:
            raise TurnPlanError("route decision and state snapshot differ")
        if accepted.registry_fingerprint != registry.fingerprint:
            raise TurnPlanError("route decision and registry differ")
        if accepted.status is RoutePolicyStatus.FAILURE:
            raise TurnPlanError("failed understanding cannot compile executable work")

        commands = accepted.commands
        mutations = tuple(
            mutation
            for command in commands
            if (mutation := self._mutation(command)) is not None
        )
        if mutations and state.flow_aggregate is None:
            raise TurnPlanError("flow mutations require authoritative flow state")
        work = self._work(commands, registry)
        route = self._route(accepted, commands, work)
        return TurnPlan(
            route=route,
            transitions=(
                FlowTransitionPlan(
                    aggregate_id=state.flow_aggregate.aggregate_id,
                    expected_aggregate_version=state.flow_aggregate.version,
                    mutations=mutations,
                )
                if state.flow_aggregate is not None and mutations else None
            ),
            work=work,
            command_ids=tuple(item.proposal.proposal_id for item in commands),
            state_fingerprint=state.fingerprint,
            registry_fingerprint=registry.fingerprint,
            compiler_version=self.compiler_version,
        )

    def _mutation(self, command: AcceptedCommand) -> FlowMutation | None:
        proposal = command.proposal
        kind = _FLOW_MUTATIONS.get(proposal.kind)
        if kind is None:
            return None
        arguments = {item.name: item for item in proposal.arguments}
        slot_update = None
        signal = None
        if proposal.kind is CommandKind.FILL_SLOT:
            slot_update = SlotUpdate(
                str(arguments["field_name"].value),
                arguments["field_value"].value_json,
            )
            signal = SignalConsumption(
                proposal.pending_signal.signal_id,
                proposal.pending_signal.signal_version,
            )
        return FlowMutation(
            kind=kind,
            command_id=proposal.proposal_id,
            source_instance_id=(
                proposal.source_flow.instance_id if proposal.source_flow else None
            ),
            expected_source_version=(
                proposal.source_flow.state_version if proposal.source_flow else None
            ),
            target_flow=proposal.target_flow,
            slot_update=slot_update,
            signal_consumption=signal,
        )

    def _work(
        self,
        commands: tuple[AcceptedCommand, ...],
        registry: FlowActionRegistry,
    ) -> WorkPlan | None:
        work_commands = tuple(item for item in commands if item.action is not None)
        if not work_commands:
            return None
        tasks = []
        items = []
        for index, command in enumerate(work_commands, start=1):
            action = command.action
            task_id = f"turn-task-{index}"
            tasks.append(TaskSpec(
                task_id=task_id,
                owner=action.owner,
                objective=action.objective,
                risk=action.risk,
                effect=action.effect,
                requirement_ids=action.requirement_ids,
                context_refs=command.proposal.evidence_refs,
            ))
            items.append(CompiledWorkItem(
                task_id=task_id,
                source_command_id=command.proposal.proposal_id,
                action_ref=action.ref,
                allowed_tools=action.allowed_tools,
                approval=action.approval,
                arguments=_work_arguments(command),
            ))
        graph = TaskGraph(
            tasks=tuple(tasks),
            primary_task_id=tasks[0].task_id,
            reason="COMMAND_PRIMARY",
            confidence=1.0,
            formation_policy_version=self.compiler_version,
            execution_policy_version=self.compiler_version,
            synthesis_policy_version=self.compiler_version,
            pinned_config_ref=registry.fingerprint,
        )
        return WorkPlan(graph, tuple(items))

    def _route(
        self,
        accepted: RoutePolicyResult,
        commands: tuple[AcceptedCommand, ...],
        work: WorkPlan | None,
    ) -> RouteDecisionV2:
        if accepted.status is RoutePolicyStatus.OUT_OF_SCOPE:
            mode = RouteMode.OUT_OF_SCOPE
        elif accepted.status is RoutePolicyStatus.CLARIFY:
            mode = RouteMode.CLARIFY
        elif work is None:
            mode = RouteMode.DIRECT
        else:
            kinds = {item.action.work_kind for item in commands if item.action}
            owners = {task.owner for task in work.graph.tasks}
            if kinds == {WorkKind.KNOWLEDGE}:
                mode = RouteMode.KNOWLEDGE_QA
            elif kinds == {WorkKind.HANDOFF}:
                mode = RouteMode.HANDOFF
            elif kinds == {WorkKind.AGENT} and len(owners) == 1:
                mode = RouteMode.AGENT_TASK
            elif kinds == {WorkKind.AGENT}:
                mode = RouteMode.MULTI_DOMAIN
            else:
                mode = RouteMode.MIXED

        tasks = work.graph.ordered_tasks if work else ()
        owners = tuple(dict.fromkeys(task.owner.value for task in tasks))
        requirements = tuple(sorted({
            requirement for task in tasks for requirement in task.requirement_ids
        }))
        risk = max(
            (_route_risk(task.risk) for task in tasks),
            default=RouteRisk.LOW,
            key=_risk_rank,
        )
        flows = {
            flow.key: flow
            for command in commands
            for flow in (
                command.proposal.source_flow.definition
                if command.proposal.source_flow else None,
                command.proposal.target_flow,
            )
            if flow is not None
        }
        return RouteDecisionV2(
            mode=mode,
            owner_ids=owners,
            requirement_ids=requirements,
            flow_refs=tuple(flows.values()),
            risk=risk,
            reason_code=accepted.reason_code,
            policy_version=accepted.policy_version,
        )


def _work_arguments(command: AcceptedCommand) -> tuple[CommandArgument, ...]:
    proposal = command.proposal
    arguments = {item.name: item for item in proposal.arguments}
    arguments.update({
        binding.name: CommandArgument(binding.name, binding.value_json)
        for binding in (
            proposal.source_flow.bindings if proposal.source_flow else ()
        )
    })
    return tuple(arguments[name] for name in sorted(arguments))


_FLOW_MUTATIONS = {
    CommandKind.START_FLOW: FlowMutationKind.START,
    CommandKind.CONTINUE_FLOW: FlowMutationKind.ADVANCE,
    CommandKind.FILL_SLOT: FlowMutationKind.FILL_SLOT,
    CommandKind.PAUSE_FLOW: FlowMutationKind.PAUSE,
    CommandKind.EXPAND_FLOW: FlowMutationKind.EXPAND,
    CommandKind.SWITCH_FLOW: FlowMutationKind.SWITCH,
    CommandKind.COMPLETE_FLOW: FlowMutationKind.COMPLETE,
    CommandKind.CANCEL_FLOW: FlowMutationKind.CANCEL,
    CommandKind.INTERRUPT_FLOW: FlowMutationKind.INTERRUPT,
}


def _route_risk(value: TaskRisk) -> RouteRisk:
    return {
        TaskRisk.LOW: RouteRisk.LOW,
        TaskRisk.MEDIUM: RouteRisk.MEDIUM,
        TaskRisk.HIGH: RouteRisk.HIGH,
    }[value]


def _risk_rank(value: RouteRisk) -> int:
    return {
        RouteRisk.LOW: 0,
        RouteRisk.MEDIUM: 1,
        RouteRisk.HIGH: 2,
        RouteRisk.CRITICAL: 3,
    }[value]


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
