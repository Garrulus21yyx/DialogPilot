"""Versioned capability definitions for the Target Architecture v1 runtime.

This registry owns the supported capability algebra.  A router or an agent may
select only entries from this bundle; neither may invent tools, risk, approval,
requirements, or workflow versions at runtime.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable

from application.authority_policy import FactRequirement, RequirementEffect


class CapabilityRegistryError(ValueError):
    """The bundle is ambiguous, incomplete, or contains dangling references."""


class CapabilityEffect(str, Enum):
    READ = "READ"
    WRITE = "WRITE"


class CapabilityRisk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ApprovalPolicy(str, Enum):
    NONE = "NONE"
    USER_COMMAND_SUFFICIENT = "USER_COMMAND_SUFFICIENT"
    EXPLICIT_CONFIRMATION_REQUIRED = "EXPLICIT_CONFIRMATION_REQUIRED"
    STRONG_AUTH_REQUIRED = "STRONG_AUTH_REQUIRED"
    OWNER_REVIEW_REQUIRED = "OWNER_REVIEW_REQUIRED"


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    version: str
    input_schema_version: str
    output_schema_version: str
    effect: CapabilityEffect
    risk: CapabilityRisk
    authority: str
    verification_profile: str
    receipt_schema_version: str = ""
    inject_identity_fields: tuple[str, ...] = ()
    concurrency_key_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(
            self.tool_id,
            self.version,
            self.input_schema_version,
            self.output_schema_version,
            self.authority,
            self.verification_profile,
        )
        _unique_nonblank(self.inject_identity_fields, "tool identity fields")
        _unique_nonblank(self.concurrency_key_fields, "tool concurrency fields")
        if self.effect is CapabilityEffect.WRITE and not self.receipt_schema_version:
            raise CapabilityRegistryError("write tool requires a receipt schema")


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    version: str
    allowed_tool_ids: tuple[str, ...]
    allowed_skill_ids: tuple[str, ...]
    model_profile: str
    context_policy: str
    verification_profile: str
    max_parallelism: int = 1
    description: str = ""
    tool_principal: str | None = None
    timeout_seconds: int = 8
    max_model_calls: int = 4

    @property
    def execution_principal(self) -> str:
        return self.tool_principal or self.agent_id

    def __post_init__(self) -> None:
        _required(
            self.agent_id,
            self.version,
            self.model_profile,
            self.context_policy,
            self.verification_profile,
        )
        _unique_nonblank(self.allowed_tool_ids, "agent tools")
        _unique_nonblank(self.allowed_skill_ids, "agent skills")
        if self.tool_principal is not None:
            _required(self.tool_principal)
        if self.max_parallelism < 1:
            raise CapabilityRegistryError("agent max_parallelism must be positive")
        if self.timeout_seconds < 1 or self.max_model_calls < 1:
            raise CapabilityRegistryError("agent execution budgets must be positive")


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    version: str
    owner_agent: str
    objective: str
    required_arguments: tuple[str, ...]
    optional_arguments: tuple[str, ...]
    requirement_ids: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    effect: CapabilityEffect
    risk: CapabilityRisk
    verification_profile: str
    persistent_flow_ref: str | None = None

    def __post_init__(self) -> None:
        _required(
            self.skill_id,
            self.version,
            self.owner_agent,
            self.objective,
            self.verification_profile,
        )
        _disjoint_unique_arguments(self.required_arguments, self.optional_arguments)
        _unique_nonblank(self.requirement_ids, "skill requirements")
        _unique_nonblank(self.allowed_tool_ids, "skill tools")


@dataclass(frozen=True)
class FlowDefinition:
    flow_id: str
    version: str
    owner_agent: str
    stages: tuple[str, ...]
    initial_stage: str
    terminal_stages: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    resumable: bool = True

    @property
    def ref(self) -> str:
        return f"{self.flow_id}:{self.version}"

    def __post_init__(self) -> None:
        _required(self.flow_id, self.version, self.owner_agent, self.initial_stage)
        _unique_nonblank(self.stages, "flow stages")
        _unique_nonblank(self.terminal_stages, "flow terminal stages")
        _unique_nonblank(self.allowed_tool_ids, "flow tools")
        if not self.stages:
            raise CapabilityRegistryError("flow requires at least one stage")
        if self.initial_stage not in self.stages:
            raise CapabilityRegistryError("flow initial stage is not declared")
        if not self.terminal_stages or not set(self.terminal_stages).issubset(self.stages):
            raise CapabilityRegistryError("flow terminal stages are invalid")


@dataclass(frozen=True)
class ActionPreparationDefinition:
    """Registry-owned binding from an authoritative read to a write approval."""

    tool_id: str
    requirement_id: str
    readiness_field: str
    readiness_value_json: str
    target_version_field: str
    target_version_argument: str

    @classmethod
    def create(
        cls,
        *,
        tool_id: str,
        requirement_id: str,
        readiness_field: str,
        readiness_value: object,
        target_version_field: str,
        target_version_argument: str,
    ) -> "ActionPreparationDefinition":
        return cls(
            tool_id,
            requirement_id,
            readiness_field,
            _canonical_json(readiness_value),
            target_version_field,
            target_version_argument,
        )

    def __post_init__(self) -> None:
        _required(
            self.tool_id,
            self.requirement_id,
            self.readiness_field,
            self.readiness_value_json,
            self.target_version_field,
            self.target_version_argument,
        )
        try:
            value = json.loads(self.readiness_value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CapabilityRegistryError(
                "action preparation readiness value must be JSON"
            ) from exc
        if _canonical_json(value) != self.readiness_value_json:
            raise CapabilityRegistryError(
                "action preparation readiness value must use canonical JSON"
            )

    @property
    def readiness_value(self) -> object:
        return json.loads(self.readiness_value_json)


@dataclass(frozen=True)
class ActionReconciliationDefinition:
    """Registry-owned read contract for resolving an unknown write outcome."""

    tool_id: str
    requirement_id: str
    operation_key_argument: str
    operation_key_field: str
    passthrough_arguments: tuple[str, ...]
    receipt_id_field: str

    def __post_init__(self) -> None:
        _required(
            self.tool_id,
            self.requirement_id,
            self.operation_key_argument,
            self.operation_key_field,
            self.receipt_id_field,
        )
        _unique_nonblank(
            self.passthrough_arguments,
            "action reconciliation passthrough arguments",
        )
        if self.operation_key_argument in self.passthrough_arguments:
            raise CapabilityRegistryError(
                "reconciliation operation key cannot be a passthrough argument"
            )


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    version: str
    owner_agent: str
    flow_ref: str | None
    effect: CapabilityEffect
    risk: CapabilityRisk
    requirement_ids: tuple[str, ...]
    allowed_tool_ids: tuple[str, ...]
    approval_policy: ApprovalPolicy
    receipt_schema_version: str
    reconciliation: ActionReconciliationDefinition
    verification_profile: str
    preparation: ActionPreparationDefinition | None = None
    interruptible_by_security: bool = False

    @property
    def ref(self) -> str:
        return f"{self.action_id}:{self.version}"

    def __post_init__(self) -> None:
        _required(
            self.action_id,
            self.version,
            self.owner_agent,
            self.receipt_schema_version,
            self.verification_profile,
        )
        if self.flow_ref is not None:
            _required(self.flow_ref)
        _unique_nonblank(self.requirement_ids, "action requirements")
        _unique_nonblank(self.allowed_tool_ids, "action tools")
        if self.effect is not CapabilityEffect.WRITE:
            raise CapabilityRegistryError("registered actions must be business writes")
        if not self.allowed_tool_ids:
            raise CapabilityRegistryError("action requires an allowed write tool")
        if self.approval_policy is ApprovalPolicy.NONE:
            raise CapabilityRegistryError("write action requires an approval policy")


@dataclass(frozen=True)
class VerificationProfile:
    profile_id: str
    version: str
    required_checks: tuple[str, ...]

    @property
    def ref(self) -> str:
        return f"{self.profile_id}:{self.version}"

    def __post_init__(self) -> None:
        _required(self.profile_id, self.version)
        _unique_nonblank(self.required_checks, "verification checks")
        if not self.required_checks:
            raise CapabilityRegistryError("verification profile requires checks")


@dataclass(frozen=True)
class CapabilityRegistryBundle:
    tenant_id: str
    bundle_version: str
    agents: tuple[AgentDefinition, ...]
    skills: tuple[SkillDefinition, ...]
    flows: tuple[FlowDefinition, ...]
    actions: tuple[ActionDefinition, ...]
    requirements: tuple[FactRequirement, ...]
    tools: tuple[ToolDefinition, ...]
    verification_profiles: tuple[VerificationProfile, ...]
    planning_shortcuts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _required(self.tenant_id, self.bundle_version)
        _unique_nonblank(self.planning_shortcuts, "planning shortcuts")
        agents = _index(self.agents, "agent_id", "agents")
        skills = _index(self.skills, "skill_id", "skills")
        flows = _index(self.flows, "ref", "flows")
        _index(self.actions, "ref", "actions")
        requirements = _index(self.requirements, "requirement_id", "requirements")
        tools = _index(self.tools, "tool_id", "tools")
        profiles = _index(self.verification_profiles, "ref", "verification profiles")

        for agent in self.agents:
            _known(agent.allowed_tool_ids, tools, f"agent {agent.agent_id} tools")
            _known(agent.allowed_skill_ids, skills, f"agent {agent.agent_id} skills")
            _get(profiles, agent.verification_profile, "agent verification profile")
        for tool in self.tools:
            _get(profiles, tool.verification_profile, "tool verification profile")
        for skill in self.skills:
            owner = _get(agents, skill.owner_agent, "skill owner")
            _known(skill.allowed_tool_ids, tools, f"skill {skill.skill_id} tools")
            _known(skill.requirement_ids, requirements, f"skill {skill.skill_id} requirements")
            _get(profiles, skill.verification_profile, "skill verification profile")
            if skill.skill_id not in owner.allowed_skill_ids:
                raise CapabilityRegistryError("skill is not exposed by its owner agent")
            if not set(skill.allowed_tool_ids).issubset(owner.allowed_tool_ids):
                raise CapabilityRegistryError("skill tools exceed owner agent allowlist")
            if skill.persistent_flow_ref is not None:
                flow = _get(flows, skill.persistent_flow_ref, "skill flow")
                if flow.owner_agent != skill.owner_agent:
                    raise CapabilityRegistryError("skill and persistent flow owners differ")
            _validate_effect(skill.effect, skill.requirement_ids, requirements)
        for flow in self.flows:
            owner = _get(agents, flow.owner_agent, "flow owner")
            _known(flow.allowed_tool_ids, tools, f"flow {flow.ref} tools")
            if not set(flow.allowed_tool_ids).issubset(owner.allowed_tool_ids):
                raise CapabilityRegistryError("flow tools exceed owner agent allowlist")
        for action in self.actions:
            owner = _get(agents, action.owner_agent, "action owner")
            flow = _get(flows, action.flow_ref, "action flow") if action.flow_ref else None
            _known(action.allowed_tool_ids, tools, f"action {action.ref} tools")
            _known(action.requirement_ids, requirements, f"action {action.ref} requirements")
            _get(profiles, action.verification_profile, "action verification profile")
            if flow is not None and flow.owner_agent != action.owner_agent:
                raise CapabilityRegistryError("action and flow owners differ")
            if flow is not None and not set(action.allowed_tool_ids).issubset(flow.allowed_tool_ids):
                raise CapabilityRegistryError("action tools exceed flow allowlist")
            if not set(action.allowed_tool_ids).issubset(owner.allowed_tool_ids):
                raise CapabilityRegistryError("action tools exceed owner agent allowlist")
            if any(tools[item].effect is not CapabilityEffect.WRITE for item in action.allowed_tool_ids):
                raise CapabilityRegistryError("action may execute only registered write tools")
            _validate_effect(action.effect, action.requirement_ids, requirements)
            reconciliation = action.reconciliation
            reconciliation_tool = _get(
                tools, reconciliation.tool_id, "action reconciliation tool",
            )
            reconciliation_requirement = _get(
                requirements,
                reconciliation.requirement_id,
                "action reconciliation requirement",
            )
            if flow is not None and reconciliation.tool_id not in flow.allowed_tool_ids:
                raise CapabilityRegistryError(
                    "action reconciliation tool exceeds flow allowlist"
                )
            # The Action grants its runtime a reconciliation read. An autonomous
            # domain planner need not receive that internal capability or supply
            # the operation key; the workflow binds it from the accepted action.
            if reconciliation_tool.effect is not CapabilityEffect.READ:
                raise CapabilityRegistryError(
                    "action reconciliation tool must be read-only"
                )
            if (
                reconciliation_requirement.effect is not RequirementEffect.READ
                or reconciliation.tool_id
                not in reconciliation_requirement.allowed_tools
            ):
                raise CapabilityRegistryError(
                    "action reconciliation requirement does not authorize its tool"
                )
            preparation = action.preparation
            if preparation is not None:
                tool = _get(tools, preparation.tool_id, "action preparation tool")
                requirement = _get(
                    requirements,
                    preparation.requirement_id,
                    "action preparation requirement",
                )
                if flow is not None and preparation.tool_id not in flow.allowed_tool_ids:
                    raise CapabilityRegistryError(
                        "action preparation tool exceeds flow allowlist"
                    )
                if preparation.tool_id not in owner.allowed_tool_ids:
                    raise CapabilityRegistryError(
                        "action preparation tool exceeds owner agent allowlist"
                    )
                if tool.effect is not CapabilityEffect.READ:
                    raise CapabilityRegistryError(
                        "action preparation tool must be read-only"
                    )
                if (
                    requirement.effect is not RequirementEffect.READ
                    or preparation.tool_id not in requirement.allowed_tools
                ):
                    raise CapabilityRegistryError(
                        "action preparation requirement does not authorize its tool"
                    )

    @property
    def fingerprint(self) -> str:
        def encode(value: object) -> object:
            if isinstance(value, Enum):
                return value.value
            if isinstance(value, tuple):
                return [encode(item) for item in value]
            if isinstance(value, dict):
                return {key: encode(item) for key, item in value.items()}
            return value

        payload = encode(asdict(self))
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return "capability-bundle:v1:" + hashlib.sha256(raw).hexdigest()

    def agent(self, agent_id: str) -> AgentDefinition:
        return _lookup(self.agents, "agent_id", agent_id, "agent")

    def skill(self, skill_id: str) -> SkillDefinition:
        return _lookup(self.skills, "skill_id", skill_id, "skill")

    def flow(self, flow_ref: str) -> FlowDefinition:
        return _lookup(self.flows, "ref", flow_ref, "flow")

    def action(self, action_ref: str) -> ActionDefinition:
        return _lookup(self.actions, "ref", action_ref, "action")

    def tool(self, tool_id: str) -> ToolDefinition:
        return _lookup(self.tools, "tool_id", tool_id, "tool")

    def requirement(self, requirement_id: str) -> FactRequirement:
        return _lookup(
            self.requirements,
            "requirement_id",
            requirement_id,
            "requirement",
        )


def _required(*values: object) -> None:
    if any(not str(value or "").strip() for value in values):
        raise CapabilityRegistryError("capability identity fields must not be blank")


def _unique_nonblank(values: Iterable[str], label: str) -> None:
    materialized = tuple(values)
    if any(not str(value).strip() for value in materialized):
        raise CapabilityRegistryError(f"{label} must not contain blank values")
    if len(materialized) != len(set(materialized)):
        raise CapabilityRegistryError(f"{label} must be unique")


def _disjoint_unique_arguments(required: tuple[str, ...], optional: tuple[str, ...]) -> None:
    _unique_nonblank(required, "required arguments")
    _unique_nonblank(optional, "optional arguments")
    if set(required).intersection(optional):
        raise CapabilityRegistryError("required and optional arguments overlap")


def _index(values: Iterable[object], field: str, label: str) -> dict[str, object]:
    result = {str(getattr(value, field)): value for value in values}
    materialized = tuple(values)
    if len(result) != len(materialized):
        raise CapabilityRegistryError(f"duplicate {label}")
    return result


def _known(values: Iterable[str], index: dict[str, object], label: str) -> None:
    unknown = set(values).difference(index)
    if unknown:
        raise CapabilityRegistryError(f"{label} contain unknown IDs: {sorted(unknown)}")


def _get(index: dict[str, object], key: str, label: str):
    try:
        return index[key]
    except KeyError as exc:
        raise CapabilityRegistryError(f"unknown {label}: {key}") from exc


def _lookup(values: Iterable[object], field: str, key: str, label: str):
    try:
        return next(value for value in values if getattr(value, field) == key)
    except StopIteration as exc:
        raise CapabilityRegistryError(f"unknown {label}: {key}") from exc


def _validate_effect(
    effect: CapabilityEffect,
    requirement_ids: Iterable[str],
    requirements: dict[str, object],
) -> None:
    expected = RequirementEffect(effect.value)
    if any(requirements[item].effect is not expected for item in requirement_ids):
        raise CapabilityRegistryError("capability and requirement effects differ")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
