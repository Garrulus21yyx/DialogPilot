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
from application.work_item import ArgumentValue, ControlMode


_IDENTIFIER = re.compile(r"\b[A-Za-z]{1,12}[-_]?\d{2,64}\b")
_ADDRESS_CHANGE = re.compile(
    r"(?:地址(?:改成|改为)|修改(?:成|为)|改成|改为|change\s+address\s+to)"
    r"\s*[:：]?\s*(?P<address>.+)$",
    re.IGNORECASE,
)


class BoundedTargetUnderstanding:
    """Route unambiguous supported shapes; abstain instead of guessing.

    This is the deterministic/fast-path provider for the vertical prototype.
    A structured LLM provider can be composed behind it for deferred cases
    without changing RoutePolicy or execution contracts.
    """

    version = "bounded-target-understanding-v1"

    async def __call__(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        if (
            deterministic.kind is ResolutionKind.FILL_PENDING_INPUT
            and deterministic.resumed_work_items
        ):
            commands = []
            for index, item in enumerate(deterministic.resumed_work_items, start=1):
                if item.control_mode is ControlMode.WORKFLOW:
                    raise ValueError("business workflows use their approval/resume contract")
                if item.control_mode is ControlMode.DIRECT:
                    if len(item.allowed_tools) != 1:
                        raise ValueError("resumed direct work must bind one tool")
                    commands.append(CommandProposal(
                        f"resume-input-{index}",
                        CommandKind.DIRECT_TOOL,
                        item.owner_agent,
                        item.objective,
                        item.arguments,
                        item.requirement_ids,
                        tool_id=item.allowed_tools[0],
                    ))
                    continue
                if item.skill_hint is not None:
                    commands.append(CommandProposal(
                        f"resume-input-{index}",
                        CommandKind.RUN_SKILL,
                        item.owner_agent,
                        item.objective,
                        item.arguments,
                        item.requirement_ids,
                        skill_id=item.skill_hint,
                    ))
                else:
                    commands.append(CommandProposal(
                        f"resume-input-{index}",
                        CommandKind.DELEGATE_TASK,
                        item.owner_agent,
                        item.objective,
                        item.arguments,
                        item.requirement_ids,
                        candidate_skill_ids=item.allowed_skills,
                    ))
            return TurnProposal(
                ProposalDisposition.RESOLVED,
                tuple(commands),
                "PENDING_INPUT_RESUMED",
            )
        if deterministic.kind in {
            ResolutionKind.APPROVAL_DECISION,
            ResolutionKind.APPROVAL_EXPIRED,
            ResolutionKind.RECONCILE_WORKFLOW,
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
            action = registry.action(str(deterministic.action_ref))
            stream = next(
                item for item in state.workstreams
                if item.workstream_id == deterministic.workstream_id
            )
            return TurnProposal(
                ProposalDisposition.RESOLVED,
                (CommandProposal(
                    "continue-approved-workflow",
                    CommandKind.CONTINUE_WORKFLOW,
                    action.owner_agent,
                    f"Execute explicitly approved action {action.action_id}",
                    arguments,
                    action.requirement_ids,
                    flow_ref=stream.flow_ref,
                    action_ref=deterministic.action_ref,
                    target_entity_ref=deterministic.target_entity_ref,
                    target_entity_version=deterministic.target_entity_version,
                    approval_binding=deterministic.signal_id,
                    approval_signal_version=deterministic.signal_version,
                    operation_key=deterministic.operation_key,
                ),),
                (
                    "RECONCILIATION_RESUME"
                    if deterministic.kind is ResolutionKind.RECONCILE_WORKFLOW
                    else "APPROVED_WORKFLOW_RESUME"
                ),
            )
        text = observations.raw_text.strip()
        lowered = text.lower()
        fields = dict(observations.structured_fields)
        identifiers = _IDENTIFIER.findall(text)
        order_id = str(fields.get("order_id") or (identifiers[0] if identifiers else ""))
        asset_id = str(fields.get("asset_id") or "")

        product_identification_signal = any(
            token in lowered for token in (
                "识别这张", "识别图片", "图里是什么", "图片型号",
                "商品型号", "产品型号", "product model",
                "identify this", "identify the product",
            )
        )
        media_text_signal = bool(asset_id) and any(
            token in lowered for token in (
                "截图文字", "图片文字", "读取截图", "识别文字", "错误码",
                "订单号", "运单号", "read the text", "error code", "ocr",
            )
        )
        compound_product_signal = product_identification_signal and any(
            token in lowered for token in ("并且", "并根据", "同时", "然后", "以及")
        )
        refund_signal = any(
            token in lowered for token in ("退款", "退掉", "退货", "refund")
        )
        invoice_signal = any(token in lowered for token in ("发票", "invoice"))
        order_signal = any(
            token in lowered for token in ("订单", "物流", "发货", "order", "shipping")
        )
        cancel_order_signal = bool(order_id) and any(
            token in lowered for token in ("取消订单", "取消这个订单", "cancel order")
        )
        address_change_signal = bool(order_id) and any(
            token in lowered for token in (
                "修改地址", "改地址", "地址改", "change address",
            )
        )
        address_match = _ADDRESS_CHANGE.search(text) if address_change_signal else None
        new_address = (
            str(fields.get("new_address") or "").strip()
            or (address_match.group("address").strip() if address_match else "")
        )
        handoff_signal = any(
            token in lowered for token in ("人工", "客服", "human agent", "representative")
        )
        security_signal = any(
            token in lowered for token in (
                "不是我操作", "账号被盗", "账户被盗", "异常登录",
                "可疑登录", "security event", "suspicious login",
                "account stolen",
            )
        )
        freeze_account_signal = any(
            token in lowered for token in (
                "冻结账号", "冻结账户", "锁定账号", "锁定账户",
                "freeze account", "lock account",
            )
        )

        commands = []
        if freeze_account_signal:
            commands.append(CommandProposal(
                "prepare-account-freeze",
                CommandKind.PREPARE_WORKFLOW,
                "account_security",
                "Check current account state before freezing the account",
                (),
                ("account.current_state",),
                flow_ref="freeze_account:v1",
                action_ref="account.freeze:v1",
                target_entity_ref=f"account:{state.user_id}",
            ))
        elif security_signal:
            commands.append(CommandProposal(
                "security-review",
                CommandKind.DIRECT_TOOL,
                "account_security",
                "Review recent account security events",
                (ArgumentValue.create("limit", 10),),
                ("account.security_events",),
                tool_id="account_security_event_list",
            ))
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
        refund_policy_signal = refund_signal and any(
            token in lowered for token in (
                "政策", "规则", "一般多久", "通常多久", "多久到账", "时效",
            )
        )
        refund_eligibility_signal = refund_signal and order_id and any(
            token in lowered for token in (
                "能退", "可以退", "可退款", "资格", "符合退款", "eligible",
            )
        )
        if invoice_signal:
            commands.append(CommandProposal(
                "invoice-policy",
                CommandKind.DIRECT_TOOL,
                "billing_refund",
                "Answer an invoice policy question",
                (ArgumentValue.create("query", text),),
                ("knowledge.active_source",),
                tool_id="knowledge_search",
            ))
        if refund_policy_signal:
            commands.append(CommandProposal(
                "refund-policy",
                CommandKind.DIRECT_TOOL,
                "billing_refund",
                "Answer a refund policy question",
                (ArgumentValue.create("query", text),),
                ("knowledge.active_source",),
                tool_id="knowledge_search",
            ))
        elif refund_eligibility_signal:
            commands.append(CommandProposal(
                "refund-eligibility",
                CommandKind.DIRECT_TOOL,
                "billing_refund",
                "Check current refund eligibility without starting a refund",
                (ArgumentValue.create("order_id", order_id),),
                ("refund.eligibility",),
                tool_id="refund_eligibility_check",
            ))
        elif refund_signal and order_id and any(
            token in lowered for token in ("状态", "进度", "到账", "status")
        ):
            commands.append(CommandProposal(
                "refund-status",
                CommandKind.DIRECT_TOOL,
                "billing_refund",
                "Query current refund status",
                (ArgumentValue.create("order_id", order_id),),
                ("refund.current_state",),
                tool_id="refund_status",
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
                flow_ref="execute_refund:v1",
                action_ref="refund.request.create:v1",
                target_entity_ref=f"order:{order_id}",
            ))
        if address_change_signal and new_address:
            commands.append(CommandProposal(
                "prepare-address-change",
                CommandKind.PREPARE_WORKFLOW,
                "order_logistics",
                "Check current order state before changing its shipping address",
                (
                    ArgumentValue.create("order_id", order_id),
                    ArgumentValue.create("new_address", new_address),
                ),
                ("order.current_state",),
                flow_ref="change_shipping_address:v1",
                action_ref="order.shipping_address.change:v1",
                target_entity_ref=f"order:{order_id}",
            ))
        elif cancel_order_signal:
            commands.append(CommandProposal(
                "prepare-order-cancellation",
                CommandKind.PREPARE_WORKFLOW,
                "order_logistics",
                "Check current order state before cancellation",
                (ArgumentValue.create("order_id", order_id),),
                ("order.current_state",),
                flow_ref="cancel_order:v1",
                action_ref="order.cancel:v1",
                target_entity_ref=f"order:{order_id}",
            ))
        elif (
            order_signal and order_id and not refund_signal
            and not address_change_signal
        ):
            commands.append(CommandProposal(
                "order-status",
                CommandKind.DIRECT_TOOL,
                "order_logistics",
                "Query current order status",
                (ArgumentValue.create("order_id", order_id),),
                ("order.current_state",),
                tool_id="order_lookup",
            ))
        if media_text_signal:
            commands.append(CommandProposal(
                "media-text-read",
                CommandKind.DIRECT_TOOL,
                "general",
                "Read visible text from the supplied media",
                (ArgumentValue.create("asset_id", asset_id),),
                ("media.visible_text",),
                tool_id="media_read",
            ))
        elif product_identification_signal and asset_id and not compound_product_signal:
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
        if refund_signal and not order_id and not refund_policy_signal:
            return TurnProposal(
                ProposalDisposition.CLARIFY,
                (),
                "ORDER_ID_REQUIRED",
                ("order_id",),
            )
        if order_signal and not order_id:
            return TurnProposal(
                ProposalDisposition.CLARIFY,
                (),
                "ORDER_ID_REQUIRED",
                ("order_id",),
            )
        if address_change_signal and not new_address:
            return TurnProposal(
                ProposalDisposition.CLARIFY,
                (),
                "NEW_ADDRESS_REQUIRED",
                ("new_address",),
            )
        if product_identification_signal and not asset_id:
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


class CascadedTargetUnderstanding:
    """Resolve cheap paths first; invoke one conversation planner only on defer."""

    version = "cascaded-target-understanding-v2"

    def __init__(self, bounded, planner, *, encoder=None) -> None:
        self._bounded = bounded
        self._planner = planner
        self._encoder = encoder

    async def __call__(
        self, observations, state, deterministic, registry, turn_context=None,
    ):
        primary = await self._bounded(
            observations, state, deterministic, registry, turn_context,
        )
        if primary.disposition is not ProposalDisposition.CLARIFY:
            return primary
        if primary.reason_code in {
            "ORDER_ID_REQUIRED", "PRODUCT_MEDIA_REQUIRED",
            "APPROVAL_DECLINED", "APPROVAL_EXPIRED",
        }:
            return primary
        if self._encoder is not None:
            decision = await self._encoder(
                observations, state, registry, turn_context,
            )
            if decision.accepted:
                return decision.proposal
        return await self._planner.plan(
            observations, state, deterministic, registry, turn_context,
        )
