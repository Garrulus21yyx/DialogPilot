"""Context-aware conversation planning behind deterministic fast paths."""
from __future__ import annotations

from dataclasses import replace
import logging
from typing import Mapping, Protocol

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
)
from application.work_item import ArgumentValue


_GOALS = {
    "cancel_active_work",
    "general_qa",
    "order_status",
    "logistics_status",
    "cancel_order",
    "change_address",
    "refund_policy",
    "refund_eligibility",
    "refund_status",
    "execute_refund",
    "invoice_qa",
    "product_identification",
    "product_qa",
    "product_assistance",
    "media_text_read",
    "media_visual_analysis",
    "human_handoff",
    "security_review",
    "freeze_account",
}
_MISSING_FIELDS = {
    "order_id", "asset_id", "new_address", "customer_service_goal",
}


logger = logging.getLogger(__name__)


class ConversationProviderOutputError(ValueError):
    """The provider responded, but its transport payload is not usable JSON."""


class ConversationPlanningProvider(Protocol):
    version: str

    async def plan(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    async def compose(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class ConversationAgent:
    """Plan one deferred turn, then compile only Registry-backed commands."""

    version = "conversation-agent-plan-v1"

    def __init__(
        self,
        provider: ConversationPlanningProvider,
        *,
        context_budget: ContextBudgetManager | None = None,
    ) -> None:
        self._provider = provider
        self._context_budget = context_budget or ContextBudgetManager()

    async def compose(
        self, payload: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Organize verified claims without reopening planning or execution."""
        budgeted = self._context_budget.fit_payload(payload)
        return await self._provider.compose(budgeted.payload)

    async def plan(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        if deterministic.kind not in {
            ResolutionKind.UNRESOLVED,
            ResolutionKind.RESUME_WORKSTREAM,
            ResolutionKind.CONTINUE_WORKSTREAM,
        }:
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "CONVERSATION_PLANNER_RECEIVED_RESOLVED_STATE",
            )
        context_evidence = (
            turn_context.understanding_evidence
            if turn_context is not None else ()
        )
        payload = {
            "schema_version": "conversation-plan-request-v1",
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
            "supported_goals": sorted(_GOALS),
            "missing_fields_schema": sorted(_MISSING_FIELDS),
            "registry_fingerprint": registry.fingerprint,
        }
        try:
            budgeted = self._context_budget.fit_payload(
                payload,
                trim_oldest_paths=("conversation_context.recent_messages",),
            )
            raw = await self._provider.plan(budgeted.payload)
        except ModelContextBudgetExceeded:
            return TurnProposal(
                ProposalDisposition.PROVIDER_FAILURE, (),
                "CONTEXT_BUDGET_EXCEEDED",
            )
        except ConversationProviderOutputError:
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "CONVERSATION_PROVIDER_OUTPUT_INVALID",
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
            return self._validate_and_compile(
                raw, observations, state, registry, turn_context,
            )
        except (KeyError, TypeError, ValueError):
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "CONVERSATION_PROVIDER_OUTPUT_INVALID",
            )

    def _validate_and_compile(
        self, raw, observations, state, registry, turn_context,
    ):
        if not isinstance(raw, Mapping):
            raise TypeError("semantic result must be an object")
        status = str(raw["status"])
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
        goals = raw.get("goals")
        if not isinstance(goals, list) or not 1 <= len(goals) <= 4:
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
            if kind not in _GOALS:
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
            if kind == "cancel_active_work":
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
            if command.tool_id == "knowledge_search":
                from application.knowledge_tool_contract import knowledge_query_options
                options = knowledge_query_options(value.get("knowledge_options", {}))
                command = replace(command, arguments=command.arguments + tuple(
                    ArgumentValue.create(key, option) for key, option in sorted(options.items())))
            elif value.get("knowledge_options"):
                raise ValueError("knowledge options belong to knowledge goals")
            if command.tool_id == "knowledge_search" and turn_context is not None and turn_context.recent_relevant_turns and not value.get("resolved_query"):
                raise ValueError("contextual knowledge goal requires an explicit resolved query")
            if revises_control_id and kind != "cancel_active_work":
                command = replace(
                    command, revises_control_id=revises_control_id,
                )
            commands.append(replace(command, dependencies=dependencies))
        return TurnProposal(
            ProposalDisposition.RESOLVED, tuple(commands), "CONVERSATION_AGENT_PLAN",
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
                CommandKind.PREPARE_WORKFLOW,
                "account_security",
                "Check current account state before freezing the account",
                (),
                ("account.current_state",),
                flow_ref="freeze_account:v1",
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
                goal_id, CommandKind.PREPARE_WORKFLOW, "order_logistics",
                "Check current order state before cancellation",
                (ArgumentValue.create("order_id", order_id),),
                ("order.current_state",),
                flow_ref="cancel_order:v1", action_ref="order.cancel:v1",
                target_entity_ref=f"order:{order_id}",
                argument_bindings=(order_binding,),
            )
        if kind == "change_address":
            if not order_id or not new_address:
                raise ValueError("address change lacks observed order ID or address")
            registry.action("order.shipping_address.change:v1")
            return CommandProposal(
                goal_id, CommandKind.PREPARE_WORKFLOW, "order_logistics",
                "Check current order state before changing its shipping address",
                (
                    ArgumentValue.create("order_id", order_id),
                    ArgumentValue.create("new_address", new_address),
                ),
                ("order.current_state",),
                flow_ref="change_shipping_address:v1",
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
            registry.flow("execute_refund:v1")
            return CommandProposal(
                goal_id, CommandKind.PREPARE_WORKFLOW, "billing_refund",
                "Check refund eligibility before a governed write",
                (
                    ArgumentValue.create("order_id", order_id),
                    ArgumentValue.create("reason", text),
                ),
                ("refund.eligibility",),
                flow_ref="execute_refund:v1", action_ref="refund.request.create:v1",
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
        registry.flow("human_handoff:v1")
        return CommandProposal(
            goal_id, CommandKind.START_WORKFLOW, "human_service",
            "Create a human-service handoff ticket",
            (
                ArgumentValue.create("summary", text),
                ArgumentValue.create("reason", "SEMANTIC_HANDOFF"),
                ArgumentValue.create("priority", "normal"),
            ),
            ("support.handoff_action",), flow_ref="human_handoff:v1",
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
            if item.field_name == field_name
            and (not value or item.value == value)
            and (not source_ref or item.source_ref == source_ref)
            and item.valid_for(state) is BindingStatus.UNIQUE
        )
        if value or source_ref:
            values = {item.value_json for item in candidates}
            if len(values) != 1:
                raise ValueError("provider selected an unknown or ambiguous entity")
            return max(candidates, key=lambda item: item.priority)
        return binding_set.resolve(field_name, state).selected


def _conversation_context_payload(turn_context):
    if turn_context is None:
        return {
            "projection_status": "UNAVAILABLE",
            "source_watermark": 0,
            "reason_codes": ["CONTEXT_NOT_PROVIDED"],
            "summary": None,
            "recent_messages": [],
            "evidence_refs": [],
        }
    summary = turn_context.summary
    return {
        "projection_status": turn_context.projection_status.value,
        "source_watermark": turn_context.source_watermark,
        "reason_codes": list(turn_context.projection_reason_codes),
        "summary": (
            {
                "content": summary.content,
                "source_ref": summary.source_ref,
                "covered_until_seq": summary.covered_until_seq,
                "producer_version": summary.producer_version,
            }
            if summary is not None else None
        ),
        "recent_messages": [
            {
                "role": item.role,
                "content": item.content,
                "source_ref": item.source_ref,
                "seq": item.seq,
                "observed_at": item.observed_at,
            }
            for item in turn_context.recent_messages
        ],
        "evidence_refs": list(turn_context.evidence_refs),
    }
