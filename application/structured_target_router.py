"""Target-native semantic routing contract behind deterministic fast paths."""
from __future__ import annotations

import re
from typing import Mapping, Protocol

from application.deterministic_resolution import ResolutionKind
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
)
from application.work_item import ArgumentValue


_IDENTIFIER = re.compile(r"\b[A-Za-z]{1,12}[-_]?\d{2,64}\b")
_GOALS = {
    "general_qa",
    "order_status",
    "logistics_status",
    "cancel_order",
    "refund_policy",
    "refund_eligibility",
    "refund_status",
    "execute_refund",
    "invoice_qa",
    "product_identification",
    "product_qa",
    "human_handoff",
}
_MISSING_FIELDS = {"order_id", "asset_id", "customer_service_goal"}


class TargetSemanticProvider(Protocol):
    version: str

    async def route(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class StructuredTargetCommandRouter:
    """Validate semantic goals, then deterministically compile Registry commands."""

    version = "structured-target-command-router-v1"

    def __init__(self, provider: TargetSemanticProvider) -> None:
        self._provider = provider

    async def __call__(self, observations, state, deterministic, registry):
        if deterministic.kind is not ResolutionKind.UNRESOLVED:
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "SEMANTIC_ROUTER_RECEIVED_RESOLVED_STATE",
            )
        payload = {
            "schema_version": "target-semantic-route-request-v1",
            "message": observations.raw_text,
            "observed_entities": dict(observations.structured_fields),
            "active_workstreams": [
                {
                    "workstream_id": item.workstream_id,
                    "owner_agent": item.owner_agent,
                    "capability_ref": item.capability_ref,
                    "status": item.status.value,
                }
                for item in state.active_workstreams
            ],
            "supported_goals": sorted(_GOALS),
            "registry_fingerprint": registry.fingerprint,
        }
        try:
            raw = await self._provider.route(payload)
        except Exception:
            return TurnProposal(
                ProposalDisposition.PROVIDER_FAILURE, (),
                "SEMANTIC_PROVIDER_FAILURE",
            )
        try:
            return self._validate_and_compile(raw, observations, state, registry)
        except (KeyError, TypeError, ValueError):
            return TurnProposal(
                ProposalDisposition.INVALID_PROVIDER_OUTPUT, (),
                "SEMANTIC_PROVIDER_OUTPUT_INVALID",
            )

    def _validate_and_compile(self, raw, observations, state, registry):
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
        observed_order = self._observed_order_id(observations)
        observed_asset = str(dict(observations.structured_fields).get("asset_id") or "")
        commands = []
        seen_ids = set()
        for index, value in enumerate(goals, start=1):
            if not isinstance(value, Mapping):
                raise TypeError("goal must be an object")
            goal_id = str(value.get("goal_id") or f"semantic-{index}")
            kind = str(value["kind"])
            if goal_id in seen_ids or kind not in _GOALS:
                raise ValueError("goal identity or kind is invalid")
            seen_ids.add(goal_id)
            order_id = str(value.get("order_id") or "")
            if order_id and order_id != observed_order:
                raise ValueError("provider invented an order ID")
            asset_id = str(value.get("asset_id") or "")
            if asset_id and asset_id != observed_asset:
                raise ValueError("provider invented an asset ID")
            commands.append(self._command(
                goal_id, kind, observations.raw_text, state,
                order_id or observed_order, asset_id or observed_asset, registry,
            ))
        return TurnProposal(
            ProposalDisposition.RESOLVED, tuple(commands), "STRUCTURED_SEMANTIC_ROUTER",
        )

    @staticmethod
    def _command(goal_id, kind, text, state, order_id, asset_id, registry):
        if kind == "general_qa":
            registry.skill("general_qa")
            return CommandProposal(
                goal_id, CommandKind.RUN_SKILL, "general", "Answer a supported FAQ",
                (ArgumentValue.create("question", text),),
                ("knowledge.active_source",), skill_id="general_qa",
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
            )
        if kind == "refund_policy":
            registry.skill("refund_policy_qa")
            return CommandProposal(
                goal_id, CommandKind.RUN_SKILL, "billing_refund",
                "Answer a refund policy question",
                (ArgumentValue.create("question", text),),
                ("knowledge.active_source",), skill_id="refund_policy_qa",
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
            )
        if kind == "refund_status":
            if not order_id:
                raise ValueError("refund status lacks observed order ID")
            registry.skill("refund_status_summary")
            return CommandProposal(
                goal_id, CommandKind.RUN_SKILL, "billing_refund",
                "Query current refund status",
                (ArgumentValue.create("order_id", order_id),),
                ("refund.current_state",), skill_id="refund_status_summary",
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
            )
        if kind == "product_qa":
            registry.skill("product_qa")
            return CommandProposal(
                goal_id, CommandKind.RUN_SKILL, "product_technical",
                "Answer a product question from governed product evidence",
                (ArgumentValue.create("question", text),),
                ("knowledge.active_source",), skill_id="product_qa",
            )
        if kind == "invoice_qa":
            registry.skill("invoice_qa")
            return CommandProposal(
                goal_id, CommandKind.RUN_SKILL, "billing_refund",
                "Answer an invoice policy question",
                (ArgumentValue.create("question", text),),
                ("knowledge.active_source",), skill_id="invoice_qa",
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
    def _observed_order_id(observations) -> str:
        structured = str(dict(observations.structured_fields).get("order_id") or "")
        if structured:
            return structured
        matches = _IDENTIFIER.findall(observations.raw_text)
        return matches[0] if matches else ""


class CascadedTargetUnderstanding:
    """Use deterministic bounded routing first and semantic routing only on defer."""

    version = "cascaded-target-understanding-v1"

    def __init__(self, bounded, semantic, *, encoder=None) -> None:
        self._bounded = bounded
        self._semantic = semantic
        self._encoder = encoder

    async def __call__(self, observations, state, deterministic, registry):
        primary = await self._bounded(observations, state, deterministic, registry)
        if primary.disposition is not ProposalDisposition.CLARIFY:
            return primary
        if primary.reason_code in {
            "ORDER_ID_REQUIRED", "PRODUCT_MEDIA_REQUIRED",
            "APPROVAL_DECLINED", "APPROVAL_EXPIRED",
        }:
            return primary
        if self._encoder is not None:
            decision = await self._encoder(observations, state, registry)
            if decision.accepted:
                return decision.proposal
        return await self._semantic(observations, state, deterministic, registry)
