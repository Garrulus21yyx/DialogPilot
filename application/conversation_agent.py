"""Context-aware conversation planning behind deterministic fast paths."""
from __future__ import annotations

from application.conversation_context import conversation_context_payload as _conversation_context_payload

from dataclasses import replace
import logging
from typing import Mapping, Protocol
from langgraph.errors import GraphBubbleUp
from jsonschema import ValidationError, SchemaError

from core.provider_context_budget import ProviderContextBudgetExceeded
from core.framework_models import ModelInvocationError

from application.context_budget import (
    ContextBudgetManager,
    ModelContextBudgetExceeded,
)
from application.deterministic_resolution import ResolutionKind
from application.entity_binding import (
    BindingStatus,
    EntityBinding,
    BindingSource,
)
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
    ApprovalDecisionProposal,
    PlanningInvariantError,
)
from application.work_item import ArgumentValue


# The compiler owns both the supported vocabulary and its planning meaning.
_GOAL_DESCRIPTIONS = {
    "atomic_read": "Execute one registered read tool using its native parameter schema; this does not authorize a business write.",
    "delegate_task": "Delegate the user's full requested domain outcome, including conditions and limits, using target_agent and objective. Prerequisite lookup or authentication is a step, not a replacement for that outcome. The domain chooses its available tools; this does not authorize an unavailable action.",
    "cancel_active_work": "Cancel one active conversation objective using its revises_control_id; this does not cancel an order.",
    "continue_active_work": "Continue an unchanged objective listed in resumable_work using its control_id as revises_control_id. An active control alone is not a resume entry. The runtime restores accepted tools and arguments; do not reconstruct them. Pending approval still requires an approval decision. For a changed scope use delegate_task with revises_control_id instead.",
    "general_qa": "Retrieve policy or FAQ evidence, including shipping, address-change rules, coupons and general procedures; performs no business action.",
    "order_status": "Read a specific order's current record; requires a bound order_id.",
    "logistics_status": "Read shipping status from a specific order record; requires a bound order_id and does not fetch carrier tracking events.",
    "cancel_order": "Prepare cancellation of a specific order when the user requests that action; requires a bound order_id. Policy questions use general_qa.",
    "change_address": "Prepare an actual shipping-address change requested by the user; requires a bound order_id and explicitly supplied new_address. Questions about whether or how changes work use general_qa.",
    "refund_policy": "Retrieve return/refund rules, after-sales conditions and the meaning of policy stages, including questions applied to a known order. An order reference does not turn a policy question into a business-state lookup. No order ID is required and no refund is started.",
    "refund_eligibility": "Read a specific order's operational refund-submission precheck (order status, configured submission window, existing application); requires a bound order_id. Does not assess gift, product-exception or refund-amount policies. Pair with refund_policy when those rules are also requested.",
    "refund_status": "Read an existing order's current refund progress; requires a bound order_id. Explaining whether one approval stage implies another outcome requires refund_policy evidence; a progress read alone does not establish that rule.",
    "execute_refund": "Prepare a refund action explicitly requested for a specific order; requires a bound order_id. General refund rules use refund_policy.",
    "invoice_qa": "Retrieve invoice rules and procedures; does not issue or modify an invoice.",
    "product_identification": "Identify a product from a supplied media asset using registered identification capabilities; requires a bound asset_id. Text-only documentation questions use product_qa.",
    "product_qa": "Retrieve product documentation or knowledge evidence for product rules, compatibility requirements and safe-use questions. Can retrieve rules about unknown prerequisites without identifying a physical product; does not require an asset_id. Actual media identification uses product_identification.",
    "product_assistance": "Identify a product from a supplied media asset and retrieve related knowledge using domain tools and skills; requires a bound asset_id. Text-only documentation questions use product_qa.",
    "media_text_read": "Read text/OCR from a supplied media asset; requires a bound asset_id.",
    "media_visual_analysis": "Analyze visual appearance, regions, layout or controls of a supplied asset; requires a bound asset_id.",
    "human_handoff": "Request transfer to human support.",
    "security_review": "Read recent account security events; does not freeze the account.",
    "freeze_account": "Prepare an account freeze explicitly requested by the user; does not merely explain account security rules.",
}
_GOALS = frozenset(_GOAL_DESCRIPTIONS)
_KNOWLEDGE_GOALS = frozenset({"general_qa", "refund_policy", "invoice_qa", "product_qa"})


def planning_goal_descriptions() -> dict[str, str]:
    return dict(_GOAL_DESCRIPTIONS)


def _available_goals(registry):
    return frozenset({"atomic_read", "delegate_task", "cancel_active_work", "continue_active_work", *registry.planning_shortcuts}) & _GOALS


_MISSING_FIELDS = {
    "order_id", "asset_id", "new_address", "customer_service_goal",
}


def planning_output_schema(supported_goals=None, knowledge_filter_contract=None) -> dict:
    """Internal interchange shape for the whole-turn planning algebra.

    The compiler still owns binding authorization, goal dependencies, capability
    lookup and contextual query requirements. This schema does not authorize a
    command or introduce partial execution for an insufficient-context turn.
    Optional goal IDs retain the compiler's deterministic generated-ID behavior.
    It is not exposed as one model tool: conversation_actions projects the
    currently available actions and translates their SDK arguments here.
    """
    from application.knowledge_tool_contract import knowledge_query_options_schema
    text = {"type": "string", "minLength": 1, "pattern": r"\S"}
    goal = {
        "type": "object", "additionalProperties": False, "required": ["kind"],
        "properties": {
            "kind": {"type": "string", "enum": sorted(_GOALS if supported_goals is None else supported_goals)},
            **{key: dict(text) for key in (
                "goal_id", "order_id", "order_id_source_ref", "asset_id",
                "asset_id_source_ref", "new_address", "revises_control_id",
                "target_agent", "objective",
            )},
            "resolved_query": {**text, "maxLength": 4000},
            "tool_id": dict(text),
            "arguments": {"type": "object"},
            "depends_on": {"type": "array", "uniqueItems": True, "items": dict(text)},
            "allow_action_proposals": {"type": "boolean", "description": "Only for kind=delegate_task; omit for every other kind. True only if this delegated objective requests a business change; false for delegated queries, counts, rules or advice. This permits preparation, never authorizes execution."},
            "knowledge_options": knowledge_query_options_schema(knowledge_filter_contract),
        },
        "allOf": [{
            "if": {"properties": {"kind": {"const": "delegate_task"}}},
            "then": {"required": ["target_agent", "objective", "allow_action_proposals"]},
            "else": {"not": {"anyOf": [
                {"required": ["objective"]},
                {"required": ["allow_action_proposals"]},
            ]}},
        }, {
            "if": {"properties": {"kind": {"const": "atomic_read"}}},
            "then": {"required": ["target_agent", "tool_id", "arguments"]},
            "else": {"not": {"anyOf": [{"required": ["tool_id"]}, {"required": ["arguments"]}]}},
        }, {"if": {"properties": {"kind": {"not": {"enum": ["atomic_read", "delegate_task"]}}}},
         "then": {"not": {"required": ["target_agent"]}}
        }, {"if": {"properties": {"kind": {"enum": sorted(_KNOWLEDGE_GOALS)}}},
             "then": {"required": ["resolved_query"]}}],
    }
    if supported_goals is not None and not set(supported_goals).intersection(_KNOWLEDGE_GOALS):
        del goal["properties"]["knowledge_options"]
    # Root object plus branch constraints works with object-tool transports.
    # Each status has one unambiguous shape; inactive fields are omitted.
    return {
        "type": "object", "additionalProperties": False, "required": ["status"],
        "properties": {
            "status": {"type": "string", "enum": [
                "resolved", "respond", "insufficient_context", "out_of_scope",
            ]},
            "goals": {"type": "array", "minItems": 1, "maxItems": 4, "items": goal},
            "response": dict(text),
            "input_values": {"type": "array", "minItems": 1, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["target_work_item_id", "field_name", "value"],
                "properties": {"target_work_item_id": dict(text), "field_name": dict(text),
                               "value": {"type": ["string", "number", "boolean"]}}}},
            "approval_decision": {"type": "object", "additionalProperties": False,
                "required": ["approval_id", "decision"], "properties": {
                    "approval_id": dict(text),
                    "decision": {"type": "string", "enum": ["approve", "decline"]}}},
            "missing_fields": {
                "type": "array", "minItems": 1, "uniqueItems": True,
                "items": {"type": "string", "enum": sorted(_MISSING_FIELDS)},
            },
        },
        "oneOf": [
            {"properties": {"status": {"const": "respond"}},
             "required": ["response"], "not": {"anyOf": [
                 {"required": ["goals"]}, {"required": ["approval_decision"]},
                 {"required": ["missing_fields"]}, {"required": ["input_values"]}]}},
            {"properties": {"status": {"const": "resolved"}},
             "anyOf": [{"required": ["goals"]}, {"required": ["approval_decision"]}, {"required": ["input_values"]}],
             "not": {"required": ["missing_fields"]}},
            {"properties": {"status": {"const": "insufficient_context"}},
             "required": ["missing_fields"], "not": {"anyOf": [{"required": ["goals"]}, {"required": ["approval_decision"]}]}},
            {"properties": {"status": {"const": "out_of_scope"}},
             "not": {"anyOf": [{"required": ["goals"]}, {"required": ["missing_fields"]}, {"required": ["approval_decision"]}]}},
        ],
        "allOf": [{"if": {"properties": {"status": {"enum": ["resolved", "insufficient_context", "out_of_scope"]}}},
                   "then": {"not": {"required": ["response"]}}},
                  {"if": {"properties": {"status": {"enum": ["respond", "insufficient_context", "out_of_scope"]}}},
                   "then": {"not": {"required": ["input_values"]}}}],
    }


logger = logging.getLogger(__name__)


class ConversationProviderOutputError(ValueError):
    """The provider responded, but its transport payload is not usable JSON."""


class ConversationPlanningProvider(Protocol):
    version: str

    async def plan(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    async def compose(self, payload: Mapping[str, object]) -> str: ...



class ConversationAgent:
    """Plan one deferred turn, then compile only Registry-backed commands."""

    version = "conversation-agent-v14-action-semantics"

    def __init__(
        self,
        provider: ConversationPlanningProvider,
        *,
        context_budget: ContextBudgetManager | None = None,
        synthesis_context_budget: ContextBudgetManager | None = None,
        tool_catalog=None,
        action_semantics=(),
    ) -> None:
        self._provider = provider
        self._tool_catalog = tool_catalog
        self._action_semantics = tuple(action_semantics)
        self._context_budget = context_budget or ContextBudgetManager()
        self._synthesis_context_budget = synthesis_context_budget or self._context_budget

    async def compose(
        self, payload: Mapping[str, object],
    ) -> str:
        """Write natural text without reopening planning or execution."""
        budgeted = self._synthesis_context_budget.fit_payload(payload)
        return await self._provider.compose(budgeted.payload)

    async def plan(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        if deterministic.kind not in {
            ResolutionKind.UNRESOLVED,
            ResolutionKind.RESUME_WORKSTREAM,
            ResolutionKind.CONTINUE_WORKSTREAM,
            ResolutionKind.REPLY_PENDING_INPUT,
        }:
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "CONVERSATION_PLANNER_RECEIVED_RESOLVED_STATE",
            )
        context_evidence = (
            turn_context.understanding_evidence
            if turn_context is not None else ()
        )
        from application.work_item import planning_continuations
        resumable = planning_continuations(state, deterministic.resumed_work_items)
        try:
            atomic_reads = self._tool_catalog(registry, state, turn_context) if self._tool_catalog else ()
        except (KeyError, TypeError, ValueError, SchemaError) as exc:
            raise PlanningInvariantError("conversation tool catalog is inconsistent") from exc
        payload = {
            "schema_version": "conversation-plan-request-v2-references",
            "knowledge_filter_contract": (turn_context.knowledge_filter_contract or None) if turn_context else None,
            "message": observations.raw_text,
            "deterministic_resolution": {
                "kind": deterministic.kind.value,
                "reason_code": deterministic.reason_code,
                "workstream_id": deterministic.workstream_id,
                "expected_workstream_version": (
                    deterministic.expected_workstream_version
                ),
            },
            "observed_entities": dict(observations.structured_fields),
            "supplied_interaction_values": [
                {"target_work_item_id": target, "field_name": name, "value": value}
                for target, name, value in observations.interaction_values],
            "pending_approval": ({
                "approval_id": state.pending_approval.approval_id,
                "version": state.pending_approval.version,
                "action_ref": state.pending_approval.action_ref,
                "arguments": {arg.name: arg.value for arg in state.pending_approval.arguments},
                "control_id": state.pending_approval.origin_control.control_id if state.pending_approval.origin_control else None,
                "typed_decision": observations.approval_decision,
            } if state.pending_approval else None),
            "pending_input": ({
                "interaction_id": state.pending_interaction.interaction_id,
                "version": state.pending_interaction.version,
                "requested_fields": [{"target_work_item_id": field.target_work_item_id,
                    "field_name": field.field_name, "value_schema": field.value_schema}
                    for field in state.pending_interaction.requested_fields],
                "objectives": [{
                    "control_id": item.control.control_id if item.control else None,
                    "objective": item.objective,
                    "owner_agent": item.owner_agent,
                    "requested_fields": [field.field_name for field in state.pending_interaction.requested_fields
                                         if field.target_work_item_id == item.work_item_id],
                } for item in state.pending_interaction.suspended_work_items],
            } if state.pending_interaction else None),
            "active_workstreams": [
                {
                    "workstream_id": item.workstream_id,
                    "owner_agent": item.owner_agent,
                    "capability_ref": item.capability_ref,
                    "phase": item.phase,
                    "status": item.status.value,
                    "state_version": item.state_version,
                    "slots": {
                        value.name: value.value for value in item.slots
                    },
                }
                for item in state.active_workstreams
            ],
            "active_work_controls": [
                {
                    "control_id": item.control_id,
                    "revision": item.revision,
                    "owner_agent": item.owner_agent,
                    "objective": item.objective,
                    "status": item.status.value,
                }
                for item in state.active_work_controls
            ],
            "resumable_work": [{"control_id": item.control.control_id,
                "revision": item.control.revision, "owner_agent": item.owner_agent,
                "objective": item.objective,
                "required_approval_id": (state.pending_approval.approval_id
                    if state.pending_approval is not None
                    and item.control == state.pending_approval.origin_control else None),
                } for item in resumable],
            "understanding_evidence": [
                {"kind": kind, "value": value}
                for kind, value in (
                    *observations.understanding_evidence,
                    *context_evidence,
                )
            ],
            "conversation_context": _conversation_context_payload(turn_context),
            "entity_bindings": (
                turn_context.entity_bindings.as_payload(state)
                if turn_context is not None else []
            ),
            "supported_goals": sorted(_available_goals(registry)),
            "atomic_reads": atomic_reads,
            "business_action_semantics": self._action_semantics,
            "goal_descriptions": {key: _GOAL_DESCRIPTIONS[key] for key in _available_goals(registry)},
            "domain_capabilities": [
                {
                    "agent_id": agent.agent_id,
                    "description": agent.description,
                    "tools": [
                        {"tool_id": tool_id, "effect": registry.tool(tool_id).effect.value}
                        for tool_id in agent.allowed_tool_ids
                    ],
                    "skills": list(agent.allowed_skill_ids),
                    "action_proposals": [
                        {"action_ref": action.ref, "effect": action.effect.value,
                         "approval_policy": action.approval_policy.value}
                        for action in registry.actions if action.owner_agent == agent.agent_id
                    ],
                }
                for agent in registry.agents
            ],
            "missing_fields_schema": sorted(_MISSING_FIELDS),
            "registry_fingerprint": registry.fingerprint,
        }
        try:
            from application.historical_context_budget import fit_historical_payload
            budgeted = fit_historical_payload(
                self._context_budget, payload,
                observation_path=('conversation_context', 'business_observations')
                    if any(read['tool_id'] == 'read_conversation_observation' for read in atomic_reads) else (),
                trim_oldest_paths=("conversation_context.recent_messages",),
            )
            raw = await self._provider.plan(budgeted.payload)
        except GraphBubbleUp:
            raise
        except (ModelContextBudgetExceeded, ProviderContextBudgetExceeded):
            return TurnProposal(
                ProposalDisposition.PROVIDER_FAILURE, (),
                "CONTEXT_BUDGET_EXCEEDED",
            )
        except ConversationProviderOutputError:
            logger.exception("Conversation planning output protocol rejected")
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "CONVERSATION_PROVIDER_OUTPUT_INVALID",
            )
        except ModelInvocationError as exc:
            # Preserve the SDK failure type. A failed planning node must not
            # become a completed checkpoint containing a retryable proposal.
            if exc.retryable:
                raise
            return TurnProposal(
                ProposalDisposition.PROVIDER_FAILURE, (),
                "CONVERSATION_PROVIDER_FAILURE",
            )
        except Exception:
            logger.exception(
                "Conversation planning provider failed provider_version=%s",
                getattr(self._provider, "version", "unknown"),
            )
            return TurnProposal(
                ProposalDisposition.PROVIDER_FAILURE, (),
                "CONVERSATION_PROVIDER_FAILURE",
            )
        try:
            proposal = self._validate_and_compile(
                raw, observations, state, registry, turn_context, deterministic.resumed_work_items,
                atomic_reads=atomic_reads,
            )
            return replace(proposal, historical_context_view=tuple(
                budgeted.payload['conversation_context'].get('business_observations', ())))
        except (KeyError, TypeError, ValueError, ValidationError):
            logger.exception("Conversation planning semantic contract rejected")
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "CONVERSATION_PROVIDER_OUTPUT_INVALID",
            )

    def _validate_and_compile(
        self, raw, observations, state, registry, turn_context, resolved_items, *, atomic_reads=(),
    ):
        if not isinstance(raw, Mapping):
            raise TypeError("semantic result must be an object")
        status = str(raw["status"])
        input_values = ()
        if "input_values" in raw:
            if status != "resolved" or state.pending_interaction is None:
                raise ValueError("input values require a current pending interaction")
            values = raw["input_values"]
            if not isinstance(values, list) or not values:
                raise ValueError("input values must be a nonempty list")
            for value in values:
                if not isinstance(value, Mapping) or set(value) != {"target_work_item_id", "field_name", "value"}:
                    raise ValueError("invalid pending field value")
            input_values = tuple((v["target_work_item_id"], v["field_name"], v["value"]) for v in values)
            expected = {(f.target_work_item_id, f.field_name) for f in state.pending_interaction.requested_fields}
            if (len({(w, f) for w, f, _ in input_values}) != len(input_values)
                    or not {(w, f) for w, f, _ in input_values} <= expected):
                raise ValueError("input values do not match pending fields")
        if status == "respond":
            if set(raw) != {"status", "response"}:
                raise ValueError("response-only turn cannot contain actions or interactions")
            return TurnProposal(ProposalDisposition.RESPOND, (), "CONVERSATION_RESPONSE",
                                response_text=raw["response"])
        if "response" in raw:
            raise ValueError("response text requires respond status")
        decision = None
        if "approval_decision" in raw:
            value = raw["approval_decision"]
            if (status != "resolved" or not isinstance(value, Mapping)
                    or set(value) != {"approval_id", "decision"}
                    or value["decision"] not in {"approve", "decline"}
                    or state.pending_approval is None
                    or value["approval_id"] != state.pending_approval.approval_id):
                raise ValueError("approval decision must reference the current pending action")
            decision = ApprovalDecisionProposal(value["approval_id"], value["decision"] == "approve")
        if status == "out_of_scope":
            return TurnProposal(
                ProposalDisposition.OUT_OF_SCOPE, (), "SEMANTIC_OUT_OF_SCOPE",
            )
        if status == "insufficient_context":
            fields = tuple(str(item) for item in raw.get("missing_fields", ()))
            if not fields or not set(fields).issubset(_MISSING_FIELDS):
                raise ValueError("invalid missing fields")
            return TurnProposal(
                ProposalDisposition.CLARIFY, (), "SEMANTIC_INSUFFICIENT_CONTEXT", fields,
            )
        if status != "resolved":
            raise ValueError("unsupported semantic status")
        goals = raw.get("goals", [])
        if not isinstance(goals, list) or not (1 <= len(goals) <= 4 or (decision or input_values) and not goals and "goals" not in raw):
            raise ValueError("semantic goals are invalid")
        goal_ids = tuple(
            str(value.get("goal_id") or f"semantic-{index}")
            if isinstance(value, Mapping) else ""
            for index, value in enumerate(goals, start=1)
        )
        if (
            any(not goal_id for goal_id in goal_ids)
            or len(goal_ids) != len(set(goal_ids))
        ):
            raise ValueError("semantic goal IDs are invalid")
        known_goal_ids = set(goal_ids)
        binding_set = turn_context.entity_bindings if turn_context is not None else None
        commands = []
        for index, value in enumerate(goals, start=1):
            if not isinstance(value, Mapping):
                raise TypeError("goal must be an object")
            goal_id = goal_ids[index - 1]
            kind = str(value["kind"])
            if "resolved_query" in value and (not isinstance(value["resolved_query"], str)
                    or not value["resolved_query"].strip() or len(value["resolved_query"]) > 4000):
                raise ValueError("resolved query must be bounded non-empty text")
            if kind not in _available_goals(registry):
                raise ValueError("goal identity or kind is invalid")
            raw_dependencies = value.get("depends_on", ())
            if (
                not isinstance(raw_dependencies, (list, tuple))
                or any(not isinstance(item, str) for item in raw_dependencies)
            ):
                raise ValueError("goal dependencies must be a list of goal IDs")
            dependencies = tuple(raw_dependencies)
            if set(dependencies).difference(known_goal_ids):
                raise ValueError("goal dependency is outside this plan")
            order_id = str(value.get("order_id") or "")
            order_binding = self._select_binding(
                binding_set, "order_id", order_id,
                str(value.get("order_id_source_ref") or ""), state,
            )
            asset_id = str(value.get("asset_id") or "")
            asset_binding = self._select_binding(
                binding_set, "asset_id", asset_id,
                str(value.get("asset_id_source_ref") or ""), state,
            )
            new_address = str(value.get("new_address") or "").strip()
            if new_address and new_address not in observations.raw_text:
                raise ValueError("provider invented a shipping address")
            address_binding = (
                EntityBinding.create(
                    "new_address", new_address,
                    source=BindingSource.CURRENT_MESSAGE,
                    source_ref="turn-message:current:new_address",
                    tenant_id=str(state.tenant_id), user_id=str(state.user_id),
                    conversation_id=str(state.conversation_id), priority=400,
                )
                if new_address else None
            )
            revises_control_id = str(value.get("revises_control_id") or "").strip()
            if revises_control_id and revises_control_id not in {
                item.control_id for item in state.active_work_controls
            }:
                raise ValueError("goal revises an inactive work control")
            if kind == "atomic_read":
                from jsonschema import Draft202012Validator
                capability = next((item for item in atomic_reads
                    if item["owner_agent"] == value["target_agent"] and item["tool_id"] == value["tool_id"]), None)
                if capability is None:
                    raise ValueError("atomic read is outside the current catalog")
                if not isinstance(value["arguments"], dict):
                    raise ValueError("atomic read arguments must be an object")
                Draft202012Validator(capability["input_schema"]).validate(value["arguments"])
                if {"approved", "approval_token"}.intersection(value["arguments"]):
                    raise ValueError("atomic read uses reserved control fields")
                command = CommandProposal(
                    goal_id, CommandKind.DIRECT_TOOL, capability["owner_agent"],
                    f"Read {capability['tool_id']}",
                    arguments=tuple(ArgumentValue.create(key, val) for key, val in value["arguments"].items()),
                    requirement_ids=tuple(capability["requirement_ids"]), tool_id=capability["tool_id"],
                    observe_result=True,
                )
            elif kind == "delegate_task":
                target_agent = value.get("target_agent")
                objective = value.get("objective")
                if not isinstance(target_agent, str) or target_agent not in {
                    agent.agent_id for agent in registry.agents
                }:
                    raise ValueError("delegation requires a registered domain")
                if not isinstance(objective, str) or not objective.strip():
                    raise ValueError("delegation requires an objective")
                bindings = tuple(binding for binding in (
                    order_binding, asset_binding, address_binding,
                ) if binding is not None)
                command = CommandProposal(
                    goal_id, CommandKind.DELEGATE_TASK, target_agent, objective,
                    arguments=tuple(ArgumentValue.create(binding.field_name, binding.value)
                                    for binding in bindings),
                    argument_bindings=bindings,
                    allow_action_proposals=value["allow_action_proposals"],
                )
            elif kind == "continue_active_work":
                from application.target_understanding import StateBoundTargetUnderstanding
                from application.work_item import planning_continuations
                candidates = planning_continuations(state, resolved_items)
                original = next((item for item in candidates
                                 if item.control and item.control.control_id == revises_control_id), None)
                if original is None:
                    raise ValueError("continuation requires a pending objective reference")
                command = replace(StateBoundTargetUnderstanding._resume_command(index, original),
                                  command_id=goal_id)
            elif kind == "cancel_active_work":
                if not revises_control_id:
                    raise ValueError("cancel goal requires an active work control")
                active = next(
                    item for item in state.active_work_controls
                    if item.control_id == revises_control_id
                )
                command = CommandProposal(
                    goal_id,
                    CommandKind.CANCEL_WORK,
                    active.owner_agent,
                    f"Cancel active objective: {active.objective}",
                    revises_control_id=revises_control_id,
                )
            else:
                command = self._command(
                    goal_id, kind, str(value.get("resolved_query") or observations.raw_text), state,
                    order_binding, asset_binding, address_binding, registry,
                )
            if command.tool_id == "knowledge_search" and kind != "atomic_read":
                from application.knowledge_tool_contract import knowledge_query_options
                options = knowledge_query_options(value.get("knowledge_options", {}),
                    (turn_context.knowledge_filter_contract or None) if turn_context else None)
                command = replace(command, arguments=command.arguments + tuple(
                    ArgumentValue.create(key, option) for key, option in sorted(options.items())))
            elif value.get("knowledge_options"):
                raise ValueError("knowledge options belong to knowledge goals")
            if command.tool_id == "knowledge_search" and kind != "atomic_read" and not value.get("resolved_query"):
                raise ValueError("knowledge goal requires an explicit resolved query")
            if revises_control_id and kind != "cancel_active_work":
                command = replace(
                    command, revises_control_id=revises_control_id,
                )
            commands.append(replace(command, dependencies=dependencies))
        return TurnProposal(
            ProposalDisposition.RESOLVED, tuple(commands), "CONVERSATION_AGENT_PLAN",
            approval_decision=decision,
            input_values=input_values,
        )

    @staticmethod
    def _command(
        goal_id, kind, text, state, order_binding, asset_binding,
        address_binding, registry,
    ):
        order_id = str(order_binding.value) if order_binding is not None else ""
        asset_id = str(asset_binding.value) if asset_binding is not None else ""
        new_address = str(address_binding.value) if address_binding is not None else ""
        bindings = tuple(
            item for item in (order_binding, asset_binding, address_binding)
            if item is not None
        )
        if kind == "general_qa":
            registry.tool("knowledge_search")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "general", "Answer a supported FAQ",
                (ArgumentValue.create("query", text),),
                ("knowledge.active_source",), tool_id="knowledge_search",
            )
        if kind == "security_review":
            registry.tool("account_security_event_list")
            return CommandProposal(
                goal_id,
                CommandKind.DIRECT_TOOL,
                "account_security",
                "Review recent account security events",
                (ArgumentValue.create("limit", 10),),
                ("account.security_events",),
                tool_id="account_security_event_list",
            )
        if kind == "freeze_account":
            registry.action("account.freeze:v1")
            return CommandProposal(
                goal_id,
                CommandKind.PREPARE_ACTION,
                "account_security",
                "Check current account state before freezing the account",
                (),
                ("account.current_state",),
                flow_ref=registry.action("account.freeze:v1").flow_ref,
                action_ref="account.freeze:v1",
                target_entity_ref=f"account:{state.user_id}",
            )
        if kind in {"order_status", "logistics_status"}:
            if not order_id:
                raise ValueError("order status lacks observed order ID")
            registry.tool("order_lookup")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "order_logistics",
                "Query current order status",
                (ArgumentValue.create("order_id", order_id),),
                ("order.current_state",), tool_id="order_lookup",
                argument_bindings=(order_binding,),
            )
        if kind == "cancel_order":
            if not order_id:
                raise ValueError("order cancellation lacks observed order ID")
            registry.action("order.cancel:v1")
            return CommandProposal(
                goal_id, CommandKind.PREPARE_ACTION, "order_logistics",
                "Check current order state before cancellation",
                (ArgumentValue.create("order_id", order_id),),
                ("order.current_state",),
                flow_ref=registry.action("order.cancel:v1").flow_ref, action_ref="order.cancel:v1",
                target_entity_ref=f"order:{order_id}",
                argument_bindings=(order_binding,),
            )
        if kind == "change_address":
            if not order_id or not new_address:
                raise ValueError("address change lacks observed order ID or address")
            registry.action("order.shipping_address.change:v1")
            return CommandProposal(
                goal_id, CommandKind.PREPARE_ACTION, "order_logistics",
                "Check current order state before changing its shipping address",
                (
                    ArgumentValue.create("order_id", order_id),
                    ArgumentValue.create("new_address", new_address),
                ),
                ("order.current_state",),
                flow_ref=registry.action("order.shipping_address.change:v1").flow_ref,
                action_ref="order.shipping_address.change:v1",
                target_entity_ref=f"order:{order_id}",
                argument_bindings=bindings,
            )
        if kind == "refund_policy":
            registry.tool("knowledge_search")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "billing_refund",
                "Answer a refund policy question",
                (ArgumentValue.create("query", text),),
                ("knowledge.active_source",), tool_id="knowledge_search",
            )
        if kind == "refund_eligibility":
            if not order_id:
                raise ValueError("refund eligibility lacks observed order ID")
            registry.tool("refund_eligibility_check")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "billing_refund",
                "Check current refund eligibility without starting a refund",
                (ArgumentValue.create("order_id", order_id),),
                ("refund.eligibility",), tool_id="refund_eligibility_check",
                argument_bindings=(order_binding,),
            )
        if kind == "refund_status":
            if not order_id:
                raise ValueError("refund status lacks observed order ID")
            registry.tool("refund_status")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "billing_refund",
                "Query current refund status",
                (ArgumentValue.create("order_id", order_id),),
                ("refund.current_state",), tool_id="refund_status",
                argument_bindings=(order_binding,),
            )
        if kind == "execute_refund":
            if not order_id:
                raise ValueError("refund execution lacks observed order ID")
            registry.action("refund.request.create:v1")
            return CommandProposal(
                goal_id, CommandKind.PREPARE_ACTION, "billing_refund",
                "Check refund eligibility before a governed write",
                (
                    ArgumentValue.create("order_id", order_id),
                    ArgumentValue.create("reason", text),
                ),
                ("refund.eligibility",),
                flow_ref=registry.action("refund.request.create:v1").flow_ref, action_ref="refund.request.create:v1",
                target_entity_ref=f"order:{order_id}",
                argument_bindings=(order_binding,),
            )
        if kind == "product_identification":
            if not asset_id:
                raise ValueError("product identification lacks observed asset")
            registry.skill("product_identification")
            return CommandProposal(
                goal_id, CommandKind.RUN_SKILL, "product_technical",
                "Identify the product from supplied media",
                (ArgumentValue.create("asset_id", asset_id),),
                ("product.canonical_model",), skill_id="product_identification",
                argument_bindings=(asset_binding,),
            )
        if kind == "product_assistance":
            if not asset_id:
                raise ValueError("product assistance lacks observed asset")
            registry.skill("product_identification")
            registry.tool("knowledge_search")
            return CommandProposal(
                goal_id,
                CommandKind.DELEGATE_TASK,
                "product_technical",
                "Identify the supplied product and answer the related product question",
                (
                    ArgumentValue.create("asset_id", asset_id),
                    ArgumentValue.create("question", text),
                ),
                ("product.canonical_model", "knowledge.active_source"),
                candidate_skill_ids=("product_identification",),
                argument_bindings=(asset_binding,),
            )
        if kind == "media_text_read":
            if not asset_id:
                raise ValueError("media text reading lacks observed asset")
            registry.tool("media_read")
            return CommandProposal(
                goal_id,
                CommandKind.DIRECT_TOOL,
                "general",
                "Read visible text from the supplied media",
                (ArgumentValue.create("asset_id", asset_id),),
                ("media.visible_text",),
                tool_id="media_read",
                argument_bindings=(asset_binding,),
            )
        if kind == "media_visual_analysis":
            if not asset_id:
                raise ValueError("visual analysis lacks observed asset")
            registry.tool("media_observe")
            return CommandProposal(
                goal_id,
                CommandKind.DELEGATE_TASK,
                "general",
                "Interpret task-relevant visual evidence from the supplied media",
                (
                    ArgumentValue.create("asset_id", asset_id),
                    ArgumentValue.create("question", text),
                ),
                ("media.visual_observation",),
                argument_bindings=(asset_binding,),
            )
        if kind == "product_qa":
            registry.tool("knowledge_search")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "product_technical",
                "Answer a product question from governed product evidence",
                (ArgumentValue.create("query", text),),
                ("knowledge.active_source",), tool_id="knowledge_search",
            )
        if kind == "invoice_qa":
            registry.tool("knowledge_search")
            return CommandProposal(
                goal_id, CommandKind.DIRECT_TOOL, "billing_refund",
                "Answer an invoice policy question",
                (ArgumentValue.create("query", text),),
                ("knowledge.active_source",), tool_id="knowledge_search",
            )
        registry.action("support.handoff.create:v1")
        return CommandProposal(
            goal_id, CommandKind.EXECUTE_ACTION, "human_service",
            "Create a human-service handoff ticket",
            (
                ArgumentValue.create("summary", text),
                ArgumentValue.create("reason", "SEMANTIC_HANDOFF"),
                ArgumentValue.create("priority", "normal"),
            ),
            ("support.handoff_action",), flow_ref=registry.action("support.handoff.create:v1").flow_ref,
            action_ref="support.handoff.create:v1",
            target_entity_ref=f"conversation:{state.conversation_id}",
            target_entity_version=f"conversation:{state.conversation_id}:v{state.version}",
        )

    @staticmethod
    def _select_binding(binding_set, field_name, value, source_ref, state):
        if binding_set is None:
            if value:
                raise ValueError("provider selected an entity without provenance")
            return None
        candidates = tuple(
            item for item in binding_set.bindings
            if (item.field_name == field_name or (
                item.field_name == "reference" and field_name in {"order_id", "asset_id"}
                and value and source_ref
            ))
            and (not value or item.value == value)
            and (not source_ref or item.source_ref == source_ref)
            and item.valid_for(state) is BindingStatus.UNIQUE
        )
        if value or source_ref:
            values = {item.value_json for item in candidates}
            if len(values) != 1:
                raise ValueError("provider selected an unknown or ambiguous entity")
            selected = max(candidates, key=lambda item: item.priority)
            return selected.select_type(field_name) if selected.field_name == "reference" else selected
        return binding_set.resolve(field_name, state).selected
