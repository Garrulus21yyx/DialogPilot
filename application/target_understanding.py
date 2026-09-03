"""Efficient bounded understanding for the first Target v1 API cutover."""
from __future__ import annotations

import re

from application.deterministic_resolution import (
    DeterministicResolution,
    ResolutionKind,
    TurnObservations,
)
from application.turn_planning import (
    CommandKind,
    CommandProposal,
    ProposalDisposition,
    TurnProposal,
)
from application.work_item import ArgumentValue


_IDENTIFIER = re.compile(r"\b[A-Za-z]{1,12}[-_]?\d{2,64}\b")


class BoundedTargetUnderstanding:
    """Route unambiguous supported shapes; abstain instead of guessing.

    This is the deterministic/fast-path provider for the vertical prototype.
    A structured LLM provider can be composed behind it for deferred cases
    without changing RoutePolicy or execution contracts.
    """

    version = "bounded-target-understanding-v1"

    async def __call__(self, observations, state, deterministic, registry):
        del registry
        if deterministic.kind in {
            ResolutionKind.APPROVAL_DECISION,
            ResolutionKind.APPROVAL_EXPIRED,
        }:
            if not deterministic.approved:
                return TurnProposal(
                    ProposalDisposition.CLARIFY,
                    (),
                    (
                        "APPROVAL_EXPIRED"
                        if deterministic.kind is ResolutionKind.APPROVAL_EXPIRED
                        else "APPROVAL_DECLINED"
                    ),
                )
            arguments = tuple(
                ArgumentValue.create(name, value)
                for name, value in deterministic.arguments
            )
            return TurnProposal(
                ProposalDisposition.RESOLVED,
                (CommandProposal(
                    "continue-approved-workflow",
                    CommandKind.CONTINUE_WORKFLOW,
                    "billing_refund",
                    "Execute the explicitly approved refund operation",
                    arguments,
                    ("refund.request_action",),
                    flow_ref="execute_refund:v1",
                    action_ref=deterministic.action_ref,
                    target_entity_ref=deterministic.target_entity_ref,
                    target_entity_version=deterministic.target_entity_version,
                    approval_binding=deterministic.signal_id,
                    approval_signal_version=deterministic.signal_version,
                    operation_key=deterministic.operation_key,
                ),),
                "APPROVED_WORKFLOW_RESUME",
            )
        text = observations.raw_text.strip()
        lowered = text.lower()
        fields = dict(observations.structured_fields)
        identifiers = _IDENTIFIER.findall(text)
        order_id = str(fields.get("order_id") or (identifiers[0] if identifiers else ""))
        asset_id = str(fields.get("asset_id") or "")

        product_signal = bool(asset_id) or any(
            token in lowered for token in ("型号", "商品", "product", "model")
        )
        refund_signal = any(token in lowered for token in ("退款", "refund"))
        order_signal = any(
            token in lowered for token in ("订单", "物流", "发货", "order", "shipping")
        )
        handoff_signal = any(
            token in lowered for token in ("人工", "客服", "human agent", "representative")
        )

        commands = []
        if handoff_signal:
            commands.append(CommandProposal(
                "human-handoff",
                CommandKind.START_WORKFLOW,
                "human_service",
                "Create a human-service handoff ticket",
                (
                    ArgumentValue.create("summary", text),
                    ArgumentValue.create("reason", "EXPLICIT_USER_HANDOFF"),
                    ArgumentValue.create("priority", "normal"),
                ),
                requirement_ids=("support.handoff_action",),
                flow_ref="human_handoff:v1",
                action_ref="support.handoff.create:v1",
                target_entity_ref=f"conversation:{state.conversation_id}",
                target_entity_version=f"conversation:{state.conversation_id}:v{state.version}",
            ))
        if refund_signal and order_id and any(
            token in lowered for token in ("状态", "进度", "到账", "status")
        ):
            commands.append(CommandProposal(
                "refund-status",
                CommandKind.RUN_SKILL,
                "billing_refund",
                "Query current refund status",
                (ArgumentValue.create("order_id", order_id),),
                ("refund.current_state",),
                skill_id="refund_status_summary",
            ))
        elif refund_signal and order_id:
            commands.append(CommandProposal(
                "prepare-refund",
                CommandKind.PREPARE_WORKFLOW,
                "billing_refund",
                "Check refund eligibility before a governed write",
                (
                    ArgumentValue.create("order_id", order_id),
                    ArgumentValue.create("reason", text),
                ),
                ("refund.eligibility",),
                tool_id="refund_eligibility_check",
                flow_ref="execute_refund:v1",
                action_ref="refund.request.create:v1",
                target_entity_ref=f"order:{order_id}",
                target_version_field="order_version",
            ))
        if order_signal and order_id and not refund_signal:
            commands.append(CommandProposal(
                "order-status",
                CommandKind.DIRECT_TOOL,
                "order_logistics",
                "Query current order status",
                (ArgumentValue.create("order_id", order_id),),
                ("order.current_state",),
                tool_id="order_lookup",
            ))
        if product_signal and asset_id:
            commands.append(CommandProposal(
                "product-identification",
                CommandKind.RUN_SKILL,
                "product_technical",
                "Identify the product from the supplied media",
                (ArgumentValue.create("asset_id", asset_id),),
                ("product.canonical_model",),
                skill_id="product_identification",
            ))

        if commands:
            return TurnProposal(
                ProposalDisposition.RESOLVED,
                tuple(commands),
                "BOUNDED_FAST_PATH",
            )
        if (refund_signal or order_signal) and not order_id:
            return TurnProposal(
                ProposalDisposition.CLARIFY,
                (),
                "ORDER_ID_REQUIRED",
                ("order_id",),
            )
        if product_signal and not asset_id:
            return TurnProposal(
                ProposalDisposition.CLARIFY,
                (),
                "PRODUCT_MEDIA_REQUIRED",
                ("asset_id",),
            )
        return TurnProposal(
            ProposalDisposition.CLARIFY,
            (),
            "SUPPORTED_GOAL_UNCLEAR",
            ("customer_service_goal",),
        )
