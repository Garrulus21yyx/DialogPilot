"""The single boundary that accepts understanding proposals for production."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from agents.orchestration_contracts import AgentType, TaskEffect, TaskRisk
from application.task_media import TaskMediaPolicy
from application.turn_state import FlowDefinitionRef, TurnStateSnapshot
from application.turn_understanding import (
    ClarificationDecision,
    CommandKind,
    CommandProposal,
    UnderstandingResult,
    UnderstandingStatus,
)


class RoutePolicyError(ValueError):
    pass


class WorkKind(str, Enum):
    AGENT = "AGENT"
    KNOWLEDGE = "KNOWLEDGE"
    HANDOFF = "HANDOFF"


class ApprovalPolicy(str, Enum):
    USER_COMMAND_SUFFICIENT = "USER_COMMAND_SUFFICIENT"
    EXPLICIT_CONFIRMATION_REQUIRED = "EXPLICIT_CONFIRMATION_REQUIRED"
    STRONG_AUTH_REQUIRED = "STRONG_AUTH_REQUIRED"


@dataclass(frozen=True)
class FlowDefinition:
    ref: FlowDefinitionRef
    allowed_commands: tuple[CommandKind, ...]


@dataclass(frozen=True)
class ArgumentDefinition:
    name: str
    description: str
    possible_values: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    version: str
    command_kind: CommandKind
    flow: FlowDefinitionRef | None
    work_kind: WorkKind
    owner: AgentType
    effect: TaskEffect
    risk: TaskRisk
    requirement_ids: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    approval: ApprovalPolicy
    objective: str
    media_policy: TaskMediaPolicy | None = None
    required_arguments: tuple[str, ...] = ()
    optional_arguments: tuple[str, ...] = ()
    argument_definitions: tuple[ArgumentDefinition, ...] = ()

    @property
    def key(self) -> tuple[CommandKind, tuple[str, str] | None]:
        return self.command_kind, self.flow.key if self.flow else None

    @property
    def ref(self) -> str:
        return f"{self.action_id}@{self.version}"


@dataclass(frozen=True)
class FlowActionRegistry:
    tenant_id: str
    generation: str
    flows: tuple[FlowDefinition, ...]
    actions: tuple[ActionDefinition, ...]

    def __post_init__(self) -> None:
        flow_keys = [item.ref.key for item in self.flows]
        action_keys = [item.key for item in self.actions]
        if len(flow_keys) != len(set(flow_keys)):
            raise RoutePolicyError("registry contains duplicate flows")
        if len(action_keys) != len(set(action_keys)):
            raise RoutePolicyError("registry contains ambiguous actions")
        for action in self.actions:
            arguments = (*action.required_arguments, *action.optional_arguments)
            if (
                any(not item.strip() for item in arguments)
                or len(arguments) != len(set(arguments))
            ):
                raise RoutePolicyError("action arguments are invalid")
            definitions = [item.name for item in action.argument_definitions]
            if len(definitions) != len(set(definitions)) or (
                definitions and set(definitions) != set(arguments)
            ):
                raise RoutePolicyError("action argument definitions are invalid")
            if (
                any(not item.strip() for item in action.allowed_tools)
                or len(action.allowed_tools) != len(set(action.allowed_tools))
            ):
                raise RoutePolicyError("action allowed tools are invalid")

    def flow(self, ref: FlowDefinitionRef) -> FlowDefinition:
        try:
            return next(item for item in self.flows if item.ref == ref)
        except StopIteration as exc:
            raise RoutePolicyError(f"unsupported flow: {ref.flow_id}") from exc

    def action_for(self, command: CommandProposal) -> ActionDefinition:
        flow = _work_flow(command)
        key = command.kind, flow.key if flow else None
        try:
            return next(item for item in self.actions if item.key == key)
        except StopIteration as exc:
            raise RoutePolicyError(
                f"no action registered for {command.kind.value}"
            ) from exc

    @property
    def fingerprint(self) -> str:
        return _fingerprint({
            "tenant_id": self.tenant_id,
            "generation": self.generation,
            "flows": [
                (item.ref.key, [command.value for command in item.allowed_commands])
                for item in self.flows
            ],
            "actions": [
                {
                    "key": (
                        item.command_kind.value,
                        item.flow.key if item.flow else None,
                    ),
                    "ref": item.ref,
                    "work_kind": item.work_kind.value,
                    "owner": item.owner.value,
                    "effect": item.effect.value,
                    "risk": item.risk.value,
                    "requirements": item.requirement_ids,
                    "tools": sorted(item.allowed_tools),
                    "required_arguments": sorted(item.required_arguments),
                    "optional_arguments": sorted(item.optional_arguments),
                    "argument_definitions": [
                        {
                            "name": definition.name,
                            "description": definition.description,
                            "possible_values": definition.possible_values,
                        }
                        for definition in item.argument_definitions
                    ],
                    "approval": item.approval.value,
                    "objective": item.objective,
                    "media_policy": (
                        {
                            "requirement_id": (
                                item.media_policy.requirement.requirement_id
                            ),
                            "media_required": (
                                item.media_policy.requirement.media_required
                            ),
                            "required": item.media_policy.requirement.required,
                            "minimum_stage": (
                                item.media_policy.requirement.minimum_stage.name
                            ),
                            "scope": item.media_policy.scope.value,
                            "omission_policies": (
                                item.media_policy.requirement
                                .allowed_omission_policy_refs
                            ),
                        }
                        if item.media_policy is not None else None
                    ),
                }
                for item in self.actions
            ],
        })


@dataclass(frozen=True)
class AcceptedCommand:
    proposal: CommandProposal
    action: ActionDefinition | None


class RoutePolicyStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class RoutePolicyResult:
    status: RoutePolicyStatus
    commands: tuple[AcceptedCommand, ...]
    state_fingerprint: str
    registry_fingerprint: str
    policy_version: str
    reason_code: str
    clarification: ClarificationDecision | None = None


class RoutePolicy:
    """Accept commands; never infer risk, tools, or authority from intent text."""

    def __init__(self, policy_version: str = "route-policy-v2") -> None:
        self.policy_version = policy_version

    def accept(
        self,
        understanding: UnderstandingResult,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> RoutePolicyResult:
        if registry.tenant_id != state.principal.tenant_id:
            raise RoutePolicyError("registry and state tenant differ")
        terminal = {
            UnderstandingStatus.CLARIFY: RoutePolicyStatus.CLARIFY,
            UnderstandingStatus.NO_SUPPORTED_FLOW: RoutePolicyStatus.OUT_OF_SCOPE,
            UnderstandingStatus.PROVIDER_FAILURE: RoutePolicyStatus.FAILURE,
            UnderstandingStatus.INVALID_PROVIDER_OUTPUT: RoutePolicyStatus.FAILURE,
        }
        if understanding.status in terminal:
            return self._result(
                terminal[understanding.status],
                (),
                understanding.reason_code,
                state,
                registry,
                understanding.clarification,
            )
        if understanding.status is UnderstandingStatus.DEFER:
            raise RoutePolicyError("DEFER must be resolved before RoutePolicy")

        proposals = understanding.commands
        if len({item.proposal_id for item in proposals}) != len(proposals):
            raise RoutePolicyError("duplicate command proposals are not accepted")
        accepted = []
        for proposal in proposals:
            self._validate_state_and_flow(proposal, state, registry)
            action = (
                registry.action_for(proposal)
                if proposal.kind in _WORK_COMMANDS else None
            )
            if action is not None:
                supplied = {item.name for item in proposal.arguments}
                inherited = {
                    item.name for item in (
                        proposal.source_flow.bindings
                        if proposal.source_flow is not None else ()
                    )
                }
                missing = set(action.required_arguments).difference(
                    supplied.union(inherited)
                )
                if missing:
                    raise RoutePolicyError(
                        "command omits required arguments: "
                        + ",".join(sorted(missing))
                    )
                allowed = set(action.required_arguments).union(
                    action.optional_arguments
                )
                if proposal.kind is CommandKind.FILL_SLOT:
                    allowed.update(("field_name", "field_value"))
                unknown = supplied.difference(allowed)
                if unknown:
                    raise RoutePolicyError(
                        "command contains unsupported arguments: "
                        + ",".join(sorted(unknown))
                    )
            accepted.append(AcceptedCommand(proposal, action))
        return self._result(
            RoutePolicyStatus.ACCEPTED,
            tuple(accepted),
            "COMMANDS_ACCEPTED",
            state,
            registry,
            None,
        )

    def _validate_state_and_flow(
        self,
        command: CommandProposal,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
    ) -> None:
        if command.source_flow is not None and command.source_flow not in state.active_flows:
            raise RoutePolicyError("source flow is not in the current state")
        if command.pending_slot is not None and command.pending_slot != state.pending_slot:
            raise RoutePolicyError("pending slot is not in the current state")
        for flow in (
            command.source_flow.definition if command.source_flow else None,
            command.target_flow,
        ):
            if flow is None:
                continue
            registered = registry.flow(flow)
            if command.kind not in registered.allowed_commands:
                raise RoutePolicyError(
                    f"{command.kind.value} is not allowed for {flow.flow_id}"
                )

    def _result(
        self,
        status: RoutePolicyStatus,
        commands: tuple[AcceptedCommand, ...],
        reason_code: str,
        state: TurnStateSnapshot,
        registry: FlowActionRegistry,
        clarification: ClarificationDecision | None,
    ) -> RoutePolicyResult:
        return RoutePolicyResult(
            status,
            commands,
            state.fingerprint,
            registry.fingerprint,
            self.policy_version,
            reason_code,
            clarification if status is RoutePolicyStatus.CLARIFY else None,
        )


_WORK_COMMANDS = frozenset({
    CommandKind.START_FLOW,
    CommandKind.CONTINUE_FLOW,
    CommandKind.FILL_SLOT,
    CommandKind.EXPAND_FLOW,
    CommandKind.SWITCH_FLOW,
    CommandKind.INTERRUPT_FLOW,
    CommandKind.ANSWER_KNOWLEDGE,
    CommandKind.REQUEST_HANDOFF,
})


def _work_flow(command: CommandProposal) -> FlowDefinitionRef | None:
    if command.kind in {
        CommandKind.START_FLOW,
        CommandKind.EXPAND_FLOW,
        CommandKind.SWITCH_FLOW,
        CommandKind.INTERRUPT_FLOW,
    }:
        return command.target_flow
    if command.source_flow is not None:
        return command.source_flow.definition
    return None


def _fingerprint(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
