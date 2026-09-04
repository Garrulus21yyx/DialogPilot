"""Registry-backed command acceptance and deterministic turn-plan compilation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum

from application.authority_policy import RequirementEffect
from application.capability_registry import (
    ActionDefinition,
    ApprovalPolicy,
    CapabilityEffect,
    CapabilityRegistryBundle,
    CapabilityRisk,
)
from application.conversation_state import ConversationState
from application.work_item import ArgumentValue, ControlMode, WorkItem, WorkPlan
from core.identity import InvocationIdentity


class TurnPlanningError(ValueError):
    pass


class CommandKind(str, Enum):
    DIRECT_TOOL = "DIRECT_TOOL"
    DELEGATE_TASK = "DELEGATE_TASK"
    RUN_SKILL = "RUN_SKILL"
    START_WORKFLOW = "START_WORKFLOW"
    PREPARE_WORKFLOW = "PREPARE_WORKFLOW"
    CONTINUE_WORKFLOW = "CONTINUE_WORKFLOW"


class ProposalDisposition(str, Enum):
    RESOLVED = "RESOLVED"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_PROVIDER_OUTPUT = "INVALID_PROVIDER_OUTPUT"


class RouteMode(str, Enum):
    DIRECT = "DIRECT"
    CLARIFY = "CLARIFY"
    KNOWLEDGE_QA = "KNOWLEDGE_QA"
    AGENT_TASK = "AGENT_TASK"
    MIXED = "MIXED"
    MULTI_DOMAIN = "MULTI_DOMAIN"
    HANDOFF = "HANDOFF"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class MutationApplyStage(str, Enum):
    PLAN_ACCEPTED = "PLAN_ACCEPTED"
    SIGNAL_CONSUMED = "SIGNAL_CONSUMED"
    WORK_ITEM_SUCCEEDED = "WORK_ITEM_SUCCEEDED"
    PUBLICATION_COMMITTED = "PUBLICATION_COMMITTED"


@dataclass(frozen=True)
class CommandProposal:
    command_id: str
    kind: CommandKind
    target_agent: str
    objective: str
    arguments: tuple[ArgumentValue, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    tool_id: str | None = None
    skill_id: str | None = None
    candidate_skill_ids: tuple[str, ...] = ()
    flow_ref: str | None = None
    action_ref: str | None = None
    dependencies: tuple[str, ...] = ()
    target_entity_ref: str | None = None
    target_entity_version: str | None = None
    approval_binding: str | None = None
    operation_key: str | None = None
    approval_signal_version: int | None = None

    def __post_init__(self) -> None:
        if any(not str(value or "").strip() for value in (
            self.command_id, self.target_agent, self.objective,
        )):
            raise TurnPlanningError("command identity, owner, and objective are required")
        _unique((item.name for item in self.arguments), "command arguments")
        _unique(self.requirement_ids, "command requirements")
        _unique(self.candidate_skill_ids, "candidate skills")
        _unique(self.dependencies, "command dependencies")
        if self.command_id in self.dependencies:
            raise TurnPlanningError("command cannot depend on itself")


@dataclass(frozen=True)
class TurnProposal:
    disposition: ProposalDisposition
    commands: tuple[CommandProposal, ...]
    reason_code: str
    missing_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise TurnPlanningError("proposal reason code is required")
        if self.disposition is ProposalDisposition.RESOLVED and not self.commands:
            raise TurnPlanningError("resolved proposal requires commands")
        if self.disposition is not ProposalDisposition.RESOLVED and self.commands:
            raise TurnPlanningError("terminal proposal cannot carry commands")


@dataclass(frozen=True)
class ValidatedCommand:
    proposal: CommandProposal
    allowed_tools: tuple[str, ...]
    allowed_skills: tuple[str, ...]
    effect: CapabilityEffect
    risk: CapabilityRisk
    verification_profile: str
    action: ActionDefinition | None = None


@dataclass(frozen=True)
class ValidatedCommandPlan:
    disposition: ProposalDisposition
    commands: tuple[ValidatedCommand, ...]
    reason_code: str
    missing_inputs: tuple[str, ...]
    state_fingerprint: str
    registry_fingerprint: str
    policy_version: str


class RoutePolicy:
    version = "route-policy-v1"

    def accept(
        self,
        proposal: TurnProposal,
        state: ConversationState,
        registry: CapabilityRegistryBundle,
    ) -> ValidatedCommandPlan:
        if str(state.tenant_id) != registry.tenant_id:
            raise TurnPlanningError("state and registry tenants differ")
        if proposal.disposition is not ProposalDisposition.RESOLVED:
            return ValidatedCommandPlan(
                proposal.disposition,
                (),
                proposal.reason_code,
                proposal.missing_inputs,
                state.fingerprint,
                registry.fingerprint,
                self.version,
            )
        command_ids = {item.command_id for item in proposal.commands}
        if len(command_ids) != len(proposal.commands):
            raise TurnPlanningError("command IDs must be unique")
        if any(set(item.dependencies).difference(command_ids) for item in proposal.commands):
            raise TurnPlanningError("command dependency is outside this proposal")
        validated = tuple(
            self._accept_command(item, registry, state) for item in proposal.commands
        )
        return ValidatedCommandPlan(
            ProposalDisposition.RESOLVED,
            validated,
            proposal.reason_code,
            (),
            state.fingerprint,
            registry.fingerprint,
            self.version,
        )

    def _accept_command(
        self,
        command: CommandProposal,
        registry: CapabilityRegistryBundle,
        state: ConversationState,
    ) -> ValidatedCommand:
        agent = registry.agent(command.target_agent)
        requirement_index = {
            item.requirement_id: item for item in registry.requirements
        }
        try:
            requirements = tuple(
                requirement_index[requirement_id]
                for requirement_id in command.requirement_ids
            )
        except KeyError as exc:
            raise TurnPlanningError(f"unknown requirement: {exc.args[0]}") from exc
        if command.kind is CommandKind.DIRECT_TOOL:
            if not command.tool_id or any((command.skill_id, command.flow_ref, command.action_ref)):
                raise TurnPlanningError("DIRECT_TOOL requires only a tool reference")
            tool = registry.tool(command.tool_id)
            if tool.tool_id not in agent.allowed_tool_ids:
                raise TurnPlanningError("direct tool is outside agent allowlist")
            self._validate_requirements(command, (tool.tool_id,), requirements)
            return ValidatedCommand(
                command,
                (tool.tool_id,),
                (),
                tool.effect,
                tool.risk,
                tool.verification_profile,
            )
        if command.kind in {CommandKind.DELEGATE_TASK, CommandKind.RUN_SKILL}:
            if any((command.tool_id, command.flow_ref, command.action_ref)):
                raise TurnPlanningError("delegated command cannot carry tool, flow, or action")
            skill_ids = (
                (command.skill_id,)
                if command.kind is CommandKind.RUN_SKILL and command.skill_id
                else (
                    command.candidate_skill_ids
                    if command.candidate_skill_ids
                    else agent.allowed_skill_ids
                )
            )
            if command.kind is CommandKind.RUN_SKILL and not skill_ids:
                raise TurnPlanningError("RUN_SKILL requires a skill")
            if not set(skill_ids).issubset(agent.allowed_skill_ids):
                raise TurnPlanningError("delegated skill is outside agent allowlist")
            skills = tuple(registry.skill(item) for item in skill_ids)
            if any(
                requirement.effect is RequirementEffect.WRITE
                for requirement in requirements
            ):
                raise TurnPlanningError("business writes must use a workflow command")
            candidate_tools = tuple(dict.fromkeys((
                *(
                    agent.allowed_tool_ids
                    if command.kind is CommandKind.DELEGATE_TASK else ()
                ),
                *(
                    tool
                    for skill in skills
                    for tool in skill.allowed_tool_ids
                ),
            )))
            tools = tuple(
                tool_id for tool_id in candidate_tools
                if registry.tool(tool_id).effect is CapabilityEffect.READ
            )
            if not tools:
                raise TurnPlanningError(
                    "delegated planning has no read-only capability"
                )
            self._validate_requirements(command, tools, requirements)
            risk = max(
                (
                    *(skill.risk for skill in skills),
                    *(registry.tool(tool_id).risk for tool_id in tools),
                ),
                default=CapabilityRisk.LOW,
                key=_risk_rank,
            )
            effect = (
                CapabilityEffect.WRITE
                if any(skill.effect is CapabilityEffect.WRITE for skill in skills)
                else CapabilityEffect.READ
            )
            if effect is CapabilityEffect.WRITE:
                raise TurnPlanningError("business writes must use START_WORKFLOW")
            return ValidatedCommand(
                command,
                tools,
                skill_ids,
                effect,
                risk,
                skills[0].verification_profile if len(skills) == 1 else agent.verification_profile,
            )
        if command.kind is CommandKind.START_WORKFLOW:
            if not command.flow_ref or not command.action_ref:
                raise TurnPlanningError("START_WORKFLOW requires flow and action")
            flow = registry.flow(command.flow_ref)
            try:
                action = next(item for item in registry.actions if item.ref == command.action_ref)
            except StopIteration as exc:
                raise TurnPlanningError("unknown action") from exc
            if flow.owner_agent != command.target_agent or action.owner_agent != command.target_agent:
                raise TurnPlanningError("workflow owner differs from target agent")
            if action.flow_ref != flow.ref:
                raise TurnPlanningError("action is not owned by the selected flow")
            if set(command.requirement_ids) != set(action.requirement_ids):
                raise TurnPlanningError("workflow requirements must match registered action")
            if not command.target_entity_ref or not command.target_entity_version:
                raise TurnPlanningError("workflow requires a versioned target entity")
            if (
                action.approval_policy is not ApprovalPolicy.USER_COMMAND_SUFFICIENT
                and not command.approval_binding
            ):
                raise TurnPlanningError("workflow requires an approval binding")
            return ValidatedCommand(
                command,
                tuple(dict.fromkeys((
                    *action.allowed_tool_ids,
                    action.reconciliation.tool_id,
                ))),
                (),
                action.effect,
                action.risk,
                action.verification_profile,
                action,
            )
        if command.kind is CommandKind.PREPARE_WORKFLOW:
            if not all((command.flow_ref, command.action_ref)):
                raise TurnPlanningError("PREPARE_WORKFLOW requires flow and action")
            if command.tool_id or command.skill_id:
                raise TurnPlanningError(
                    "workflow preparation capability must come from the action registry"
                )
            flow = registry.flow(str(command.flow_ref))
            action = next(
                (item for item in registry.actions if item.ref == command.action_ref), None,
            )
            if action is None or action.flow_ref != flow.ref:
                raise TurnPlanningError("preparation action is not owned by flow")
            preparation = action.preparation
            if preparation is None:
                raise TurnPlanningError("action has no registered preparation contract")
            tool = registry.tool(preparation.tool_id)
            if flow.owner_agent != command.target_agent or action.owner_agent != command.target_agent:
                raise TurnPlanningError("preparation owner differs from workflow")
            if tool.effect is not CapabilityEffect.READ:
                raise TurnPlanningError("workflow preparation must be read-only")
            if tool.tool_id not in agent.allowed_tool_ids:
                raise TurnPlanningError("preparation tool is outside agent allowlist")
            if command.requirement_ids != (preparation.requirement_id,):
                raise TurnPlanningError(
                    "workflow preparation requirement differs from action registry"
                )
            if not command.target_entity_ref:
                raise TurnPlanningError("workflow preparation requires a target entity")
            self._validate_requirements(command, (tool.tool_id,), requirements)
            return ValidatedCommand(
                command, (tool.tool_id,), (), CapabilityEffect.READ,
                max((tool.risk, action.risk), key=_risk_rank),
                tool.verification_profile, action,
            )
        if command.kind is CommandKind.CONTINUE_WORKFLOW:
            if not all((
                command.flow_ref, command.action_ref, command.operation_key,
                command.approval_binding, command.approval_signal_version,
                command.target_entity_ref, command.target_entity_version,
            )):
                raise TurnPlanningError("workflow continuation bindings are incomplete")
            signal = (
                f"approval:{command.approval_binding}:"
                f"v{command.approval_signal_version}"
            )
            if signal not in state.consumed_signal_ids:
                raise TurnPlanningError("workflow continuation has no consumed approval")
            grant = next((
                item for item in state.accepted_approvals
                if item.approval_id == command.approval_binding
                and item.version == command.approval_signal_version
            ), None)
            if grant is None:
                raise TurnPlanningError("workflow continuation has no accepted approval")
            if (
                grant.action_ref,
                grant.operation_key,
                grant.target_entity_ref,
                grant.target_entity_version,
            ) != (
                command.action_ref,
                command.operation_key,
                command.target_entity_ref,
                command.target_entity_version,
            ):
                raise TurnPlanningError(
                    "workflow continuation differs from accepted approval"
                )
            if command.arguments != grant.arguments:
                raise TurnPlanningError(
                    "workflow continuation arguments differ from accepted approval"
                )
            if not any(
                item.workstream_id == grant.workstream_id
                for item in state.active_workstreams
            ):
                raise TurnPlanningError("approved workstream is not active")
            flow = registry.flow(str(command.flow_ref))
            action = next(
                (item for item in registry.actions if item.ref == command.action_ref), None,
            )
            if action is None or action.flow_ref != flow.ref:
                raise TurnPlanningError("continuation action is not owned by flow")
            if flow.owner_agent != command.target_agent or action.owner_agent != command.target_agent:
                raise TurnPlanningError("continuation owner differs from workflow")
            if set(command.requirement_ids) != set(action.requirement_ids):
                raise TurnPlanningError("continuation requirements differ from action")
            return ValidatedCommand(
                command,
                tuple(dict.fromkeys((
                    *action.allowed_tool_ids,
                    action.reconciliation.tool_id,
                ))),
                (), action.effect, action.risk,
                action.verification_profile, action,
            )
        raise TurnPlanningError("unsupported command kind")

    @staticmethod
    def _validate_requirements(command, allowed_tools, requirements) -> None:
        if len(requirements) != len(command.requirement_ids):
            raise TurnPlanningError("unknown requirement")
        if any(not set(item.allowed_tools).intersection(allowed_tools) for item in requirements):
            raise TurnPlanningError("capability cannot satisfy a declared requirement")


@dataclass(frozen=True)
class FlowMutation:
    mutation_id: str
    kind: str
    flow_ref: str
    workstream_id: str
    expected_state_version: int
    apply_stage: MutationApplyStage
    bound_work_item_id: str
    action_ref: str | None = None
    target_entity_ref: str | None = None
    preparation_requirement_id: str | None = None
    readiness_field: str | None = None
    readiness_value_json: str | None = None
    target_version_field: str | None = None
    target_version_argument: str | None = None


@dataclass(frozen=True)
class FlowTransitionPlan:
    expected_conversation_version: int
    mutations: tuple[FlowMutation, ...]


@dataclass(frozen=True)
class RouteDecision:
    mode: RouteMode
    owner_ids: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    risk: CapabilityRisk
    missing_inputs: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True)
class TurnPlan:
    route: RouteDecision
    work: WorkPlan | None
    transitions: FlowTransitionPlan | None
    state_fingerprint: str
    registry_fingerprint: str
    compiler_version: str

    @property
    def plan_id(self) -> str:
        raw = json.dumps({
            "route": self.route.mode.value,
            "owners": self.route.owner_ids,
            "requirements": self.route.requirement_ids,
            "work": [item.fingerprint for item in self.work.items] if self.work else [],
            "mutations": [item.mutation_id for item in self.transitions.mutations]
            if self.transitions else [],
            "state": self.state_fingerprint,
            "registry": self.registry_fingerprint,
            "compiler": self.compiler_version,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "turn-plan:v1:" + hashlib.sha256(raw).hexdigest()


class TurnPlanCompiler:
    version = "turn-plan-compiler-v1"

    def compile(
        self,
        validated: ValidatedCommandPlan,
        state: ConversationState,
        registry: CapabilityRegistryBundle,
        invocation: InvocationIdentity,
    ) -> TurnPlan:
        if validated.state_fingerprint != state.fingerprint:
            raise TurnPlanningError("validated command uses stale conversation state")
        if validated.registry_fingerprint != registry.fingerprint:
            raise TurnPlanningError("validated command uses another registry bundle")
        if validated.disposition is not ProposalDisposition.RESOLVED:
            route = RouteDecision(
                RouteMode.CLARIFY if validated.disposition in {
                    ProposalDisposition.CLARIFY,
                    ProposalDisposition.PROVIDER_FAILURE,
                    ProposalDisposition.INVALID_PROVIDER_OUTPUT,
                } else RouteMode.OUT_OF_SCOPE,
                (), (), CapabilityRisk.LOW,
                validated.missing_inputs,
                validated.reason_code,
            )
            return TurnPlan(
                route, None, None, state.fingerprint, registry.fingerprint, self.version,
            )
        items = tuple(
            self._compile_item(
                index, item, state, invocation, registry.fingerprint,
            )
            for index, item in enumerate(validated.commands, start=1)
        )
        id_by_command = {
            command.proposal.command_id: item.work_item_id
            for command, item in zip(validated.commands, items)
        }
        items = tuple(
            WorkItem(**{
                **item.__dict__,
                "dependencies": tuple(id_by_command[value] for value in command.proposal.dependencies),
            })
            for command, item in zip(validated.commands, items)
        )
        work = WorkPlan(items, items[0].work_item_id)
        transitions = tuple(
            FlowMutation(
                f"mutation:{item.proposal.command_id}",
                "START",
                item.proposal.flow_ref,
                f"workstream:{item.proposal.command_id}",
                0,
                MutationApplyStage.PLAN_ACCEPTED,
                work_item.work_item_id,
                item.proposal.action_ref,
                item.proposal.target_entity_ref,
                (
                    item.action.preparation.requirement_id
                    if item.action and item.action.preparation else None
                ),
                (
                    item.action.preparation.readiness_field
                    if item.action and item.action.preparation else None
                ),
                (
                    item.action.preparation.readiness_value_json
                    if item.action and item.action.preparation else None
                ),
                (
                    item.action.preparation.target_version_field
                    if item.action and item.action.preparation else None
                ),
                (
                    item.action.preparation.target_version_argument
                    if item.action and item.action.preparation else None
                ),
            )
            for item, work_item in zip(validated.commands, items)
            if item.proposal.kind in {
                CommandKind.START_WORKFLOW,
                CommandKind.PREPARE_WORKFLOW,
            }
        )
        owners = tuple(dict.fromkeys(item.owner_agent for item in items))
        requirements = tuple(dict.fromkeys(
            requirement for item in items for requirement in item.requirement_ids
        ))
        route = RouteDecision(
            _project_mode(items),
            owners,
            requirements,
            max((item.risk for item in items), key=_risk_rank),
            (),
            validated.reason_code,
        )
        return TurnPlan(
            route,
            work,
            FlowTransitionPlan(state.version, transitions) if transitions else None,
            state.fingerprint,
            registry.fingerprint,
            self.version,
        )

    def _compile_item(
        self,
        index,
        command,
        state,
        invocation,
        registry_fingerprint,
    ) -> WorkItem:
        proposal = command.proposal
        work_item_id = f"work:{index}:{proposal.command_id}"
        control_mode = {
            CommandKind.DIRECT_TOOL: ControlMode.DIRECT,
            CommandKind.DELEGATE_TASK: ControlMode.DELEGATED,
            CommandKind.RUN_SKILL: ControlMode.DELEGATED,
            CommandKind.START_WORKFLOW: ControlMode.WORKFLOW,
            CommandKind.PREPARE_WORKFLOW: ControlMode.DIRECT,
            CommandKind.CONTINUE_WORKFLOW: ControlMode.WORKFLOW,
        }[proposal.kind]
        action = command.action
        write = command.effect is CapabilityEffect.WRITE
        return WorkItem(
            work_item_id=work_item_id,
            owner_agent=proposal.target_agent,
            objective=proposal.objective,
            control_mode=control_mode,
            allowed_tools=command.allowed_tools,
            allowed_skills=command.allowed_skills,
            arguments=proposal.arguments,
            requirement_ids=proposal.requirement_ids,
            dependencies=(),
            effect=command.effect,
            risk=command.risk,
            expected_output_schema=(
                f"{action.receipt_schema_version}" if action and write
                else "agent-result-v1"
            ),
            verification_profile=command.verification_profile,
            state_snapshot_version=state.version,
            registry_fingerprint=registry_fingerprint,
            timeout_seconds=15 if write else 8,
            max_steps=8 if write else (1 if control_mode is ControlMode.DIRECT else 4),
            skill_hint=proposal.skill_id if proposal.kind is CommandKind.RUN_SKILL else None,
            flow_ref=(
                proposal.flow_ref
                if proposal.kind in {CommandKind.START_WORKFLOW, CommandKind.CONTINUE_WORKFLOW}
                else None
            ),
            operation_key=(
                proposal.operation_key or str(invocation.operation_key(
                    proposal.target_agent,
                    action.action_id,
                    proposal.target_entity_ref,
                )) if action and write else None
            ),
            approval_binding=(
                proposal.approval_binding
                or f"user-command:{invocation.invocation_key}"
                if action and write else None
            ),
            target_entity_version=proposal.target_entity_version if write else None,
            reconciliation=action.reconciliation if action and write else None,
            aggregate_ref=proposal.target_entity_ref if write else None,
            action_ref=action.ref if action and write else None,
            approval_policy=action.approval_policy if action and write else None,
        )


def _project_mode(items: tuple[WorkItem, ...]) -> RouteMode:
    owners = {item.owner_agent for item in items}
    modes = {item.control_mode for item in items}
    if len(items) == 1 and modes == {ControlMode.DIRECT}:
        return RouteMode.DIRECT
    if len(owners) > 1 and modes == {ControlMode.DELEGATED}:
        return RouteMode.MULTI_DOMAIN
    if len(owners) == 1 and ControlMode.DIRECT not in modes:
        return RouteMode.AGENT_TASK
    return RouteMode.MIXED


def _risk_rank(value: CapabilityRisk) -> int:
    return {
        CapabilityRisk.LOW: 0,
        CapabilityRisk.MEDIUM: 1,
        CapabilityRisk.HIGH: 2,
        CapabilityRisk.CRITICAL: 3,
    }[value]


def _unique(values: object, label: str) -> None:
    materialized = tuple(values)  # type: ignore[arg-type]
    if len(materialized) != len(set(materialized)):
        raise TurnPlanningError(f"{label} must be unique")
