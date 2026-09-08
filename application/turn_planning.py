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
from application.entity_binding import BindingStatus, EntityBinding
from application.work_item import (
    ArgumentValue,
    ControlMode,
    WorkControlBinding,
    WorkItem,
    WorkPlan,
)
from core.identity import InvocationIdentity


class TurnPlanningError(ValueError):
    """A proposed command cannot be accepted under the current contract."""


class PlanningInvariantError(RuntimeError):
    """Trusted planning inputs disagree; changing model output cannot repair them."""


class PlanningUnavailable(RuntimeError):
    """Understanding failed; there is no user clarification or executable plan."""

    def __init__(self, disposition, reason_code):
        super().__init__(reason_code)
        self.disposition = disposition
        self.reason_code = reason_code


class CommandKind(str, Enum):
    DIRECT_TOOL = "DIRECT_TOOL"
    DELEGATE_TASK = "DELEGATE_TASK"
    RUN_SKILL = "RUN_SKILL"
    EXECUTE_ACTION = "EXECUTE_ACTION"
    PREPARE_ACTION = "PREPARE_ACTION"
    CONTINUE_ACTION = "CONTINUE_ACTION"
    CANCEL_WORK = "CANCEL_WORK"


class ProposalDisposition(str, Enum):
    RESPOND = "RESPOND"
    RESOLVED = "RESOLVED"
    CLARIFY = "CLARIFY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_PROVIDER_OUTPUT = "INVALID_PROVIDER_OUTPUT"


class RouteMode(str, Enum):
    RESPONSE = "RESPONSE"
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
class WorkControlMutation:
    control_id: str
    expected_revision: int
    kind: str = "CANCEL"

    def __post_init__(self) -> None:
        if not self.control_id.strip() or self.expected_revision < 1:
            raise TurnPlanningError("work control mutation binding is invalid")
        if self.kind != "CANCEL":
            raise TurnPlanningError("unsupported work control mutation")


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
    argument_bindings: tuple[EntityBinding, ...] = ()
    revises_control_id: str | None = None
    continuation_of: str | None = None
    allow_action_proposals: bool = False
    # Internal state-bound continuation; never supplied by the planning model.
    resumed_work_item: WorkItem | None = None
    # Host-set provenance for a native main-agent read, not a model parameter.
    observe_result: bool = False

    def __post_init__(self) -> None:
        if type(self.observe_result) is not bool or (
            self.observe_result and self.kind is not CommandKind.DIRECT_TOOL
        ):
            raise TurnPlanningError("main observation requires a direct tool command")
        if not isinstance(self.allow_action_proposals, bool):
            raise TurnPlanningError("action proposal scope must be boolean")
        if self.allow_action_proposals and self.kind is not CommandKind.DELEGATE_TASK:
            raise TurnPlanningError("action proposal scope requires open delegation")
        if self.resumed_work_item is not None and self.continuation_of != self.resumed_work_item.work_item_id:
            raise TurnPlanningError("resumed envelope requires its bound continuation")
        if any(not str(value or "").strip() for value in (
            self.command_id, self.target_agent, self.objective,
        )):
            raise TurnPlanningError("command identity, owner, and objective are required")
        _unique((item.name for item in self.arguments), "command arguments")
        _unique(
            (item.field_name for item in self.argument_bindings),
            "command argument bindings",
        )
        arguments = {item.name: item.value_json for item in self.arguments}
        if any(
            item.field_name not in arguments
            or arguments[item.field_name] != item.value_json
            for item in self.argument_bindings
        ):
            raise TurnPlanningError("command binding does not match its argument")
        _unique(self.requirement_ids, "command requirements")
        _unique(self.candidate_skill_ids, "candidate skills")
        _unique(self.dependencies, "command dependencies")
        if self.command_id in self.dependencies:
            raise TurnPlanningError("command cannot depend on itself")
        if self.revises_control_id is not None and not self.revises_control_id.strip():
            raise TurnPlanningError("revised control ID must be absent or nonblank")


@dataclass(frozen=True)
class ApprovalDecisionProposal:
    approval_id: str
    approved: bool

    def __post_init__(self):
        if not self.approval_id.strip() or type(self.approved) is not bool:
            raise TurnPlanningError("approval decision requires a bound identity and Boolean decision")


@dataclass(frozen=True)
class TurnProposal:
    disposition: ProposalDisposition
    commands: tuple[CommandProposal, ...]
    reason_code: str
    missing_inputs: tuple[str, ...] = ()
    approval_decision: ApprovalDecisionProposal | None = None
    response_text: str | None = None
    input_values: tuple[tuple[str, str, object], ...] = ()

    def __post_init__(self) -> None:
        if self.disposition is ProposalDisposition.RESPOND:
            if not isinstance(self.response_text, str) or not self.response_text.strip() or self.missing_inputs:
                raise TurnPlanningError("response proposal requires only nonblank response text")
        elif self.response_text is not None:
            raise TurnPlanningError("only response proposals carry response text")
        if not self.reason_code.strip():
            raise TurnPlanningError("proposal reason code is required")
        if self.input_values and self.disposition is not ProposalDisposition.RESOLVED:
            raise TurnPlanningError("input values require a resolved proposal")
        if self.disposition is ProposalDisposition.RESOLVED and not (self.commands or self.approval_decision or self.input_values):
            raise TurnPlanningError("resolved proposal requires commands or an approval decision")
        if self.disposition is not ProposalDisposition.RESOLVED and (self.commands or self.approval_decision):
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
    allowed_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatedCommandPlan:
    disposition: ProposalDisposition
    commands: tuple[ValidatedCommand, ...]
    reason_code: str
    missing_inputs: tuple[str, ...]
    state_fingerprint: str
    registry_fingerprint: str
    policy_version: str
    response_text: str | None = None


class RoutePolicy:
    version = "route-policy-v2"

    def accept(
        self,
        proposal: TurnProposal,
        state: ConversationState,
        registry: CapabilityRegistryBundle,
        *,
        planning_state: ConversationState | None = None,
        continuation_items: tuple[WorkItem, ...] = (),
    ) -> ValidatedCommandPlan:
        planning_state = planning_state or state
        if (planning_state.tenant_id, planning_state.user_id, planning_state.conversation_id) != (
                state.tenant_id, state.user_id, state.conversation_id):
            raise PlanningInvariantError("planning snapshot belongs to another conversation")
        # Envelopes originate in persisted waits/grants or the resolver's accepted
        # typed input, never in the proposal being checked.
        saved_items = (
            *(item for pending in (state.pending_interaction, state.pending_approval)
              if pending is not None for item in pending.suspended_work_items),
            *(item for grant in state.accepted_approvals for item in grant.suspended_work_items))
        accepted_items = {}
        for item in saved_items:
            key = (item.work_item_id, item.control)
            if key in accepted_items and accepted_items[key] != item:
                raise PlanningInvariantError("persisted continuation envelopes conflict")
            accepted_items[key] = item
        # Explicit items are supplied by the resolver after validating this
        # turn's input. A typed field replaces its old value, not its capabilities.
        for item in continuation_items:
            key = (item.work_item_id, item.control)
            previous = accepted_items.get(key)
            if previous is not None and any(
                    value != getattr(item, name) for name, value in previous.__dict__.items()
                    if name not in {"arguments", "argument_bindings"}):
                raise PlanningInvariantError("resolved input changed its work envelope")
            accepted_items[key] = item
        continuation_items = tuple(accepted_items.values())
        if proposal.approval_decision is not None:
            raise PlanningInvariantError("approval decision must be bound before command validation")
        if proposal.input_values:
            raise PlanningInvariantError("input values must be bound before command validation")
        if str(state.tenant_id) != registry.tenant_id:
            raise PlanningInvariantError("state and registry tenants differ")
        if proposal.disposition is not ProposalDisposition.RESOLVED:
            return ValidatedCommandPlan(
                proposal.disposition,
                (),
                proposal.reason_code,
                proposal.missing_inputs,
                state.fingerprint,
                registry.fingerprint,
                self.version,
                response_text=proposal.response_text,
            )
        command_ids = {item.command_id for item in proposal.commands}
        if len(command_ids) != len(proposal.commands):
            raise TurnPlanningError("command IDs must be unique")
        if any(set(item.dependencies).difference(command_ids) for item in proposal.commands):
            raise TurnPlanningError("command dependency is outside this proposal")
        validated = tuple(
            self._accept_command(item, registry, state, planning_state, continuation_items)
            for item in proposal.commands
        )
        security_preempted = (
            any(item.proposal.target_agent == "account_security" for item in validated)
            and any(
                item.action is not None and item.action.interruptible_by_security
                for item in validated
            )
        )
        if security_preempted:
            validated = tuple(
                item for item in validated
                if item.action is None or not item.action.interruptible_by_security
            )
        return ValidatedCommandPlan(
            ProposalDisposition.RESOLVED,
            validated,
            (
                "SECURITY_PREEMPTED_NONESSENTIAL_WRITES"
                if security_preempted else proposal.reason_code
            ),
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
        planning_state: ConversationState,
        continuation_items: tuple[WorkItem, ...],
    ) -> ValidatedCommand:
        agent = registry.agent(command.target_agent)
        if command.revises_control_id is not None:
            control = next((
                item for item in state.active_work_controls
                if item.control_id == command.revises_control_id
            ), None)
            if control is None:
                raise TurnPlanningError("command revises an inactive work control")
        if command.continuation_of is not None:
            if (command.revises_control_id is None
                    or command.kind not in {CommandKind.DELEGATE_TASK, CommandKind.DIRECT_TOOL, CommandKind.RUN_SKILL}
                    or control.work_item_id != command.continuation_of
                    or control.owner_agent != command.target_agent
                    or control.objective != command.objective):
                raise TurnPlanningError("continuation must bind the active originating objective")
            original = command.resumed_work_item
            if original is None or original not in continuation_items:
                raise TurnPlanningError("continuation lacks its accepted work envelope")
            if (original.control is None
                    or original.control.control_id != command.revises_control_id
                    or original.control.revision != control.revision
                    or original.work_item_id != command.continuation_of
                    or original.owner_agent != command.target_agent
                    or original.objective != command.objective
                    or original.registry_fingerprint != registry.fingerprint
                    or command.arguments != original.arguments
                    or command.argument_bindings != original.argument_bindings
                    or command.observe_result != original.observe_result
                    or command.requirement_ids != original.requirement_ids):
                raise TurnPlanningError("continuation parameters differ from accepted work")
            if command.kind is CommandKind.DIRECT_TOOL and original.allowed_tools != (command.tool_id,):
                raise TurnPlanningError("continuation tool differs from accepted work")
            if command.kind is CommandKind.RUN_SKILL and original.skill_hint != command.skill_id:
                raise TurnPlanningError("continuation skill differs from accepted work")
        if command.kind is CommandKind.CANCEL_WORK:
            if command.revises_control_id is None:
                raise TurnPlanningError("cancel command requires an active work control")
            return ValidatedCommand(
                command, (), (), CapabilityEffect.READ, CapabilityRisk.LOW,
                "work-control-v1",
            )
        if any(not item.belongs_to(state) for item in command.argument_bindings):
            raise TurnPlanningError("command carries a stale or unauthorized binding")
        if (command.continuation_of is None and command.kind is not CommandKind.CONTINUE_ACTION
                and any(item.valid_for(planning_state) is not BindingStatus.UNIQUE
                        for item in command.argument_bindings)):
            raise TurnPlanningError("command carries a stale or unauthorized binding")
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
            if command.observe_result and tool.effect is not CapabilityEffect.READ:
                raise TurnPlanningError("main observation is restricted to registered reads")
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
            open_delegation = (
                command.kind is CommandKind.DELEGATE_TASK
                and not command.requirement_ids
                and not command.candidate_skill_ids
            )
            resumed = command.resumed_work_item
            skill_ids = (
                (command.skill_id,)
                if command.kind is CommandKind.RUN_SKILL and command.skill_id
                else (
                    command.candidate_skill_ids
                    if command.candidate_skill_ids
                    else agent.allowed_skill_ids
                )
            )
            if resumed is not None:
                skill_ids = resumed.allowed_skills
                if not set(resumed.allowed_actions).issubset(
                        action.ref for action in registry.actions if action.owner_agent == agent.agent_id):
                    raise TurnPlanningError("resumed actions exceed registry ownership")
            if command.kind is CommandKind.RUN_SKILL and not skill_ids:
                raise TurnPlanningError("RUN_SKILL requires a skill")
            if not set(skill_ids).issubset(agent.allowed_skill_ids):
                raise TurnPlanningError("delegated skill is outside agent allowlist")
            skills = tuple(registry.skill(item) for item in skill_ids)
            if open_delegation:
                skills = tuple(skill for skill in skills if skill.effect is CapabilityEffect.READ)
                skill_ids = tuple(skill.skill_id for skill in skills)
            if any(
                requirement.effect is RequirementEffect.WRITE
                for requirement in requirements
            ):
                raise TurnPlanningError("business writes must use a workflow command")
            candidate_tools = tuple(dict.fromkeys((
                *(agent.allowed_tool_ids if open_delegation else ()),
                *(
                    tool_id
                    for requirement in requirements
                    for tool_id in requirement.allowed_tools
                ),
                *(
                    tool
                    for skill in skills
                    for tool in skill.allowed_tool_ids
                ),
            )))
            if resumed is not None:
                candidate_tools = resumed.allowed_tools
            if not set(candidate_tools).issubset(agent.allowed_tool_ids):
                raise TurnPlanningError(
                    "delegated requirement tool is outside agent allowlist"
                )
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
                raise TurnPlanningError("business writes must use EXECUTE_ACTION")
            return ValidatedCommand(
                command,
                tools,
                skill_ids,
                effect,
                risk,
                skills[0].verification_profile if len(skills) == 1 else agent.verification_profile,
                allowed_actions=(resumed.allowed_actions if resumed is not None else tuple(
                    action.ref for action in registry.actions
                    if action.owner_agent == agent.agent_id
                ) if open_delegation and command.allow_action_proposals else ()),
            )
        if command.kind is CommandKind.EXECUTE_ACTION:
            if not command.action_ref:
                raise TurnPlanningError("EXECUTE_ACTION requires an action")
            flow = registry.flow(command.flow_ref) if command.flow_ref else None
            try:
                action = next(item for item in registry.actions if item.ref == command.action_ref)
            except StopIteration as exc:
                raise TurnPlanningError("unknown action") from exc
            if (flow is not None and flow.owner_agent != command.target_agent) or action.owner_agent != command.target_agent:
                raise TurnPlanningError("workflow owner differs from target agent")
            if action.flow_ref != command.flow_ref:
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
        if command.kind is CommandKind.PREPARE_ACTION:
            if not command.action_ref:
                raise TurnPlanningError("PREPARE_ACTION requires an action")
            if command.tool_id or command.skill_id:
                raise TurnPlanningError(
                    "workflow preparation capability must come from the action registry"
                )
            flow = registry.flow(command.flow_ref) if command.flow_ref else None
            action = next(
                (item for item in registry.actions if item.ref == command.action_ref), None,
            )
            if action is None or action.flow_ref != command.flow_ref:
                raise TurnPlanningError("preparation action is not owned by flow")
            preparation = action.preparation
            if preparation is None:
                raise TurnPlanningError("action has no registered preparation contract")
            tool = registry.tool(preparation.tool_id)
            if (flow is not None and flow.owner_agent != command.target_agent) or action.owner_agent != command.target_agent:
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
        if command.kind is CommandKind.CONTINUE_ACTION:
            if not all((
                command.action_ref, command.operation_key,
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
            if any(work.registry_fingerprint != registry.fingerprint for work in grant.suspended_work_items):
                raise TurnPlanningError("action continuation uses another registry version")
            origin = next((work for work in grant.suspended_work_items
                           if work.work_item_id == grant.origin_work_item_id), None)
            binding = grant.control or (origin.control if origin is not None else None)
            if binding is not None:
                if not any(control.control_id == binding.control_id and control.revision == binding.revision
                           for control in state.active_work_controls):
                    raise TurnPlanningError("approved action objective was superseded")
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
            if command.arguments != grant.arguments or command.argument_bindings != grant.argument_bindings:
                raise TurnPlanningError(
                    "workflow continuation arguments differ from accepted approval"
                )
            if not any(
                item.workstream_id == grant.workstream_id
                for item in state.active_workstreams
            ):
                raise TurnPlanningError("approved workstream is not active")
            flow = registry.flow(command.flow_ref) if command.flow_ref else None
            action = next(
                (item for item in registry.actions if item.ref == command.action_ref), None,
            )
            if action is None or action.flow_ref != command.flow_ref:
                raise TurnPlanningError("continuation action is not owned by flow")
            if (flow is not None and flow.owner_agent != command.target_agent) or action.owner_agent != command.target_agent:
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
    flow_ref: str | None
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

    def __post_init__(self):
        for name in ("owner_ids", "requirement_ids", "missing_inputs"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class TurnPlan:
    route: RouteDecision
    work: WorkPlan | None
    transitions: FlowTransitionPlan | None
    state_fingerprint: str
    registry_fingerprint: str
    compiler_version: str
    control_mutations: tuple[WorkControlMutation, ...] = ()
    response_text: str | None = None
    # A dependent task already owns the next decision for a consumed result.
    observation_work_item_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "control_mutations", tuple(self.control_mutations))
        object.__setattr__(self, "observation_work_item_ids", tuple(self.observation_work_item_ids))
        _unique(self.observation_work_item_ids, "observation work items")
        items = {item.work_item_id: item for item in self.work.items} if self.work else {}
        consumed = {dependency for item in items.values() for dependency in item.dependencies}
        for identity in self.observation_work_item_ids:
            item = items.get(identity)
            if (item is None or item.control_mode is not ControlMode.DIRECT
                    or item.effect is not CapabilityEffect.READ or identity in consumed):
                raise TurnPlanningError("main observation requires an unconsumed direct read")
        if self.route.mode is RouteMode.RESPONSE:
            if (not isinstance(self.response_text, str) or not self.response_text.strip()
                    or self.work is not None or self.transitions is not None or self.control_mutations):
                raise TurnPlanningError("response plan requires text without work or mutations")
        elif self.response_text is not None:
            raise TurnPlanningError("only response plans carry response text")

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
            "response_text": self.response_text,
            "observation_work_item_ids": self.observation_work_item_ids,
            "control_mutations": [
                (item.kind, item.control_id, item.expected_revision)
                for item in self.control_mutations
            ],
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "turn-plan:v1:" + hashlib.sha256(raw).hexdigest()


class TurnPlanCompiler:
    version = "turn-plan-compiler-v5-durable-observation"

    def compile(
        self,
        validated: ValidatedCommandPlan,
        state: ConversationState,
        registry: CapabilityRegistryBundle,
        invocation: InvocationIdentity,
        *, planning_step: int = 0,
    ) -> TurnPlan:
        if type(planning_step) is not int or planning_step < 0:
            raise PlanningInvariantError("planning step must be a nonnegative integer")
        if validated.state_fingerprint != state.fingerprint:
            raise PlanningInvariantError("validated command uses stale conversation state")
        if validated.registry_fingerprint != registry.fingerprint:
            raise PlanningInvariantError("validated command uses another registry bundle")
        if validated.disposition in {ProposalDisposition.PROVIDER_FAILURE,
                                     ProposalDisposition.INVALID_PROVIDER_OUTPUT}:
            raise PlanningUnavailable(validated.disposition, validated.reason_code)
        if validated.disposition is not ProposalDisposition.RESOLVED:
            route = RouteDecision(
                RouteMode.RESPONSE if validated.disposition is ProposalDisposition.RESPOND
                else RouteMode.CLARIFY if validated.disposition is ProposalDisposition.CLARIFY
                else RouteMode.OUT_OF_SCOPE,
                (), (), CapabilityRisk.LOW,
                validated.missing_inputs,
                validated.reason_code,
            )
            return TurnPlan(
                route, None, None, state.fingerprint, registry.fingerprint, self.version,
                response_text=validated.response_text,
            )
        executable = tuple(
            item for item in validated.commands
            if item.proposal.kind is not CommandKind.CANCEL_WORK
        )
        cancellations = tuple(
            item for item in validated.commands
            if item.proposal.kind is CommandKind.CANCEL_WORK
        )
        items = tuple(
            self._compile_item(
                index, item, state, invocation, registry, planning_step,
            )
            for index, item in enumerate(executable, start=1)
        )
        id_by_command = {
            command.proposal.command_id: item.work_item_id
            for command, item in zip(executable, items)
        }
        items = tuple(
            WorkItem(**{
                **item.__dict__,
                "dependencies": tuple(id_by_command[value] for value in command.proposal.dependencies),
            })
            for command, item in zip(executable, items)
        )
        from application.work_item import WorkItemContractError
        try:
            work = WorkPlan(items, items[0].work_item_id) if items else None
        except WorkItemContractError as exc:
            raise TurnPlanningError(f"invalid command dependency plan: {exc}") from exc
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
            for item, work_item in zip(executable, items)
            if item.proposal.kind in {
                CommandKind.EXECUTE_ACTION,
                CommandKind.PREPARE_ACTION,
            }
        )
        owners = tuple(dict.fromkeys(
            item.proposal.target_agent for item in validated.commands
        ))
        requirements = tuple(dict.fromkeys(
            requirement for item in items for requirement in item.requirement_ids
        ))
        route = RouteDecision(
            _project_mode(items) if items else RouteMode.DIRECT,
            owners,
            requirements,
            max((item.risk for item in items), key=_risk_rank)
            if items else CapabilityRisk.LOW,
            (),
            "WORK_CANCELLED" if cancellations and not items else validated.reason_code,
        )
        return TurnPlan(
            route,
            work,
            FlowTransitionPlan(state.version, transitions) if transitions else None,
            state.fingerprint,
            registry.fingerprint,
            self.version,
            tuple(
                WorkControlMutation(
                    str(item.proposal.revises_control_id),
                    next(
                        control.revision for control in state.active_work_controls
                        if control.control_id == item.proposal.revises_control_id
                    ),
                )
                for item in cancellations
            ),
            observation_work_item_ids=tuple(
                item.work_item_id for item in items
                if item.observe_result and not any(
                    item.work_item_id in consumer.dependencies for consumer in items)
            ),
        )

    def _compile_item(
        self,
        index,
        command,
        state,
        invocation,
        registry,
        planning_step,
    ) -> WorkItem:
        proposal = command.proposal
        agent = registry.agent(proposal.target_agent)
        work_item_id = f"work:{invocation.invocation_key}:step:{planning_step}:{index}:{proposal.command_id}"
        control_mode = {
            CommandKind.DIRECT_TOOL: ControlMode.DIRECT,
            CommandKind.DELEGATE_TASK: ControlMode.DELEGATED,
            CommandKind.RUN_SKILL: ControlMode.DELEGATED,
            CommandKind.EXECUTE_ACTION: ControlMode.WORKFLOW,
            CommandKind.PREPARE_ACTION: ControlMode.DIRECT,
            CommandKind.CONTINUE_ACTION: ControlMode.WORKFLOW,
        }[proposal.kind]
        action = command.action
        write = command.effect is CapabilityEffect.WRITE
        if write and proposal.flow_ref is None:
            control_mode = ControlMode.ACTION
        control = self._control_binding(proposal, state, invocation, planning_step)
        replay = next((
            item for item in state.work_controls
            if item.control_id == control.control_id
            and item.revision == control.revision
            and item.invocation_key == str(invocation.invocation_key)
        ), None)
        return WorkItem(
            work_item_id=work_item_id,
            owner_agent=proposal.target_agent,
            objective=proposal.objective,
            control_mode=control_mode,
            allowed_tools=command.allowed_tools,
            allowed_skills=command.allowed_skills,
            arguments=proposal.arguments,
            argument_bindings=proposal.argument_bindings,
            requirement_ids=proposal.requirement_ids,
            dependencies=(),
            effect=command.effect,
            risk=command.risk,
            expected_output_schema=(
                f"{action.receipt_schema_version}" if action and write
                else "agent-result-v1"
            ),
            verification_profile=command.verification_profile,
            state_snapshot_version=(
                replay.state_snapshot_version if replay is not None else state.version
            ),
            registry_fingerprint=registry.fingerprint,
            timeout_seconds=15 if write else agent.timeout_seconds,
            max_steps=8 if write else (1 if control_mode is ControlMode.DIRECT else agent.max_model_calls),
            skill_hint=proposal.skill_id if proposal.kind is CommandKind.RUN_SKILL else None,
            flow_ref=(
                proposal.flow_ref
                if proposal.kind in {CommandKind.EXECUTE_ACTION, CommandKind.CONTINUE_ACTION}
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
            control=control,
            allowed_actions=command.allowed_actions,
            continuation_of=proposal.continuation_of,
            observe_result=proposal.observe_result,
        )

    @staticmethod
    def _control_binding(proposal, state, invocation, planning_step) -> WorkControlBinding:
        if proposal.revises_control_id is None:
            return WorkControlBinding(
                f"control:{invocation.invocation_key}:step:{planning_step}:{proposal.command_id}", 1,
            )
        current = next(
            item for item in state.active_work_controls
            if item.control_id == proposal.revises_control_id
        )
        return WorkControlBinding(current.control_id, current.revision + 1)


def _project_mode(items: tuple[WorkItem, ...]) -> RouteMode:
    owners = {item.owner_agent for item in items}
    modes = {item.control_mode for item in items}
    if len(items) == 1 and items[0].requirement_ids == (
        "support.handoff_action",
    ):
        return RouteMode.HANDOFF
    if (
        len(items) == 1
        and items[0].control_mode is ControlMode.DIRECT
        and items[0].allowed_tools == ("knowledge_search",)
    ):
        return RouteMode.KNOWLEDGE_QA
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
