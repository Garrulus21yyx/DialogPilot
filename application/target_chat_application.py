"""Protocol-neutral `/chat` facade for Target Architecture v1."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Mapping, Protocol

from application.agent_result import AgentResultStatus
from application.chat_contracts import ChatCommand, ChatOutcome, Completed, Conflict, Failed, NeedsInput, Reconciling, Rejected
from application.deterministic_resolution import (
    DeterministicResolutionError,
    TurnObservations,
)
from application.target_conversation_manager import TargetConversationManager
from application.response_assembly import ResponseAssembler, ResponseAssemblyMode
from application.turn_runtime import TurnRuntime
from application.turn_planning import ProposalDisposition
from core.identity import IdentityContractError, IdentityFactory, InvocationIdentity
from application.work_item import WorkControlBinding


logger = logging.getLogger(__name__)


class TargetAdmissionStatus(str, Enum):
    CREATED = "CREATED"
    EXISTING = "EXISTING"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class TargetAdmission:
    status: TargetAdmissionStatus
    existing: Mapping[str, object] | None = None


@dataclass(frozen=True)
class PublishedTargetResponse:
    response_id: str
    response_seq: int
    delivery_status: str


class TargetAdmissionPort(Protocol):
    def admit(
        self,
        command: ChatCommand,
        identity: InvocationIdentity,
        *,
        bundle_version: str,
    ) -> TargetAdmission: ...


class TargetPublicationPort(Protocol):
    def has_interaction(self, identity: InvocationIdentity, *, signal_id: str, signal_version: int) -> bool: ...

    def completed(
        self,
        identity: InvocationIdentity,
    ) -> Completed | NeedsInput | None: ...

    def publish(
        self,
        identity: InvocationIdentity,
        *,
        response_text: str,
        public_response: Mapping[str, object],
        bundle_version: str,
        evidence_sha256: str,
        verifier_status: str,
        expected_work_controls: tuple[WorkControlBinding, ...] = (),
        execution_stages: tuple = (),
    ) -> PublishedTargetResponse: ...

    def publish_interaction(
        self,
        identity: InvocationIdentity,
        *,
        signal_id: str,
        signal_version: int,
        challenge: str,
        resume_schema: Mapping[str, object],
        expires_at: str,
        expected_work_controls: tuple[WorkControlBinding, ...] = (),
    ) -> PublishedTargetResponse: ...


class TargetChatApplication:
    """Admission -> one ConversationManager -> one Publication boundary."""

    version = "target-chat-application-v1"

    def __init__(
        self,
        *,
        manager: TargetConversationManager,
        admission: TargetAdmissionPort,
        publication: TargetPublicationPort,
        bundle_version: str,
        identity_factory: IdentityFactory | None = None,
        response_assembler: ResponseAssembler | None = None,
        turn_runtime: TurnRuntime | None = None,
        knowledge_context_factory=None,
    ) -> None:
        self._knowledge_context_factory = knowledge_context_factory
        self._admission = admission
        self._publication = publication
        self._bundle_version = bundle_version
        self._identity_factory = identity_factory or IdentityFactory()
        assembler = response_assembler or ResponseAssembler()
        self._response_locale = assembler.fallback_locale
        self._turn_runtime = turn_runtime or TurnRuntime(manager, assembler)

    async def handle(self, command: ChatCommand) -> ChatOutcome:
        started = time.monotonic()
        try:
            identity = self.identity_for(command)
        except IdentityContractError:
            return Rejected("invalid_invocation_identity", "请求身份字段无效")

        admission = self.admit(command, identity)
        if admission.status is TargetAdmissionStatus.CONFLICT:
            return Conflict("IDEMPOTENCY_CONFLICT", admission.existing or {})
        if admission.status is TargetAdmissionStatus.EXISTING:
            completed = self.completed(identity)
            if completed is not None:
                return completed
        return await self.execute_admitted(command, identity, started_at=started)

    def identity_for(self, command: ChatCommand) -> InvocationIdentity:
        return self._identity_factory.create_invocation(
            tenant_id=command.tenant_id,
            user_id=command.user_id,
            conversation_id=command.conv_id,
            request_id=command.request_id,
            continuation_id=command.continuation_id,
        )

    def admit(
        self, command: ChatCommand, identity: InvocationIdentity,
    ) -> TargetAdmission:
        return self._admission.admit(
            command,
            identity,
            bundle_version=self._bundle_version,
        )

    def completed(
        self, identity: InvocationIdentity,
    ) -> Completed | NeedsInput | None:
        return self._publication.completed(identity)

    async def execute_admitted(
        self,
        command: ChatCommand,
        identity: InvocationIdentity,
        *,
        started_at: float | None = None,
    ) -> ChatOutcome:
        """Execute one already-durable invocation without admitting it again."""
        started = started_at if started_at is not None else time.monotonic()

        try:
            observations = TurnObservations(
                command.message,
                tuple(
                    ("asset_id", asset_id)
                    for asset_id in command.asset_ids[:1]
                ),
                approval_decision=command.approval_decision,
                approval_id=command.approval_id,
                interaction_id=command.interaction_id,
                interaction_version=command.interaction_version,
                interaction_values=command.interaction_values,
            )
            execution_context = {
                "authorization_fingerprint": command.authorization_fingerprint,
                **(self._knowledge_context_factory() if self._knowledge_context_factory else {}),
            }
            turn_result = await self._turn_runtime.execute(
                identity, observations, execution_context=execution_context,
            )
            managed = turn_result.managed
        except DeterministicResolutionError as exc:
            return Conflict(
                (
                    "INTERACTION_SIGNAL_CONFLICT"
                    if command.interaction_id is not None
                    or command.interaction_values
                    else "APPROVAL_SIGNAL_CONFLICT"
                ),
                {"reason": str(exc)},
            )
        except Exception as exc:
            from core.framework_models import ModelInvocationError
            from application.turn_runtime import InteractionAssemblyUnavailable, TurnCheckpointVersionError
            if isinstance(exc, TurnCheckpointVersionError):
                return Failed("turn_checkpoint_version_unsupported", False,
                    str(identity.invocation_key),
                    "This earlier task needs reconciliation before it can continue.")
            if isinstance(exc, ModelInvocationError):
                return Failed("conversation_provider_unavailable",
                    exc.retryable and self._turn_runtime.supports_resume,
                    str(identity.invocation_key),
                    "The planning service could not complete this request.")
            if isinstance(exc, InteractionAssemblyUnavailable):
                return Failed("target_interaction_" + exc.reason.lower(), exc.retryable,
                    str(identity.invocation_key), "The follow-up question could not be prepared. Progress is saved.",
                    stages=exc.diagnostics)
            logger.exception(
                "Target turn execution failed invocation_key=%s",
                identity.invocation_key,
            )
            return Failed(
                "target_runtime_failed",
                False,
                str(identity.invocation_key),
                f"Target runtime failed closed: {type(exc).__name__}",
            )

        pending = managed.state_after.pending_approval
        assembly = turn_result.assembled
        if pending is not None and assembly is not None and (
            assembly.approval_operation_key == pending.operation_key
            and assembly.verified_text_sha256
        ) and (
            managed.state_before.pending_approval is None
            or managed.state_before.pending_approval.approval_id != pending.approval_id
            or not self._publication.has_interaction(identity,
                signal_id=pending.approval_id, signal_version=pending.version)
        ):
            expires_at = pending.expires_at
            published = self._publication.publish_interaction(
                identity,
                signal_id=pending.approval_id,
                signal_version=pending.version,
                challenge=assembly.text,
                resume_schema={
                    "type": "object",
                    "required": ["approval_id", "approved"],
                    "properties": {
                        "approval_id": {"const": pending.approval_id},
                        "approved": {"type": "boolean"},
                    },
                },
                expires_at=expires_at,
                expected_work_controls=tuple(
                    item.control for item in pending.suspended_work_items if item.control),
            )
            return NeedsInput(
                str(identity.workflow_run_id),
                pending.approval_id,
                "APPROVAL",
                expires_at,
                published.response_id,
            )

        pending_input = managed.state_after.pending_interaction
        if pending_input is not None and (
            managed.state_before.pending_interaction is None
            or managed.state_before.pending_interaction.interaction_id
            != pending_input.interaction_id
        ):
            if assembly is None:
                return Failed("target_interaction_not_assembled", True,
                    str(identity.invocation_key), "The follow-up question could not be prepared.")
            if assembly.composer_used and not assembly.verified:
                return Failed("target_interaction_not_verified", True,
                    str(identity.invocation_key), "The follow-up question could not be verified.")
            challenge = assembly.text
            expires_at = (
                datetime.now(timezone.utc) + timedelta(days=7)
            ).isoformat()
            published = self._publication.publish_interaction(
                identity,
                signal_id=pending_input.interaction_id,
                signal_version=pending_input.version,
                challenge=challenge,
                resume_schema={
                    "interaction_kind": "FIELDS",
                    "type": "object",
                    "required": [
                        "interaction_id", "interaction_version", "values",
                    ],
                    "properties": {
                        "interaction_id": {"const": pending_input.interaction_id},
                        "interaction_version": {"const": pending_input.version},
                        "values": {
                            "type": "array",
                            "items": [
                                {
                                    "target_work_item_id": field.target_work_item_id,
                                    "field_name": field.field_name,
                                    "value_schema": field.value_schema,
                                }
                                for field in pending_input.requested_fields
                            ],
                        },
                    },
                },
                expires_at=expires_at,
            )
            return NeedsInput(
                str(identity.workflow_run_id),
                pending_input.interaction_id,
                "FIELDS",
                expires_at,
                published.response_id,
            )

        if managed.plan.work is None:
            failure = {
                "CONTEXT_BUDGET_EXCEEDED": (
                    "context_budget_exceeded",
                    False,
                    "当前请求包含的必要上下文过长，请缩小本次处理范围。",
                ),
                "CONVERSATION_PROVIDER_FAILURE": (
                    "conversation_provider_unavailable",
                    False,
                    "语义服务暂时不可用，请稍后重试。",
                ),
                "CONVERSATION_PROVIDER_OUTPUT_INVALID": (
                    "conversation_provider_output_invalid",
                    False,
                    "语义服务返回了无效结果，本次未执行任何操作。",
                ),
            }.get(managed.plan.route.reason_code)
            if failure is not None:
                code, retryable, message = failure
                return Failed(
                    code,
                    retryable,
                    str(identity.invocation_key),
                    message,
                )

        handoff_receipt = None
        if managed.plan.work is None:
            disposition = managed.plan.route.mode.value
            response_text = _terminal_response(managed.plan.route.reason_code, locale=self._response_locale)
            verifier_status = "NOT_CHECKED"
            coverage_complete = not managed.plan.route.missing_inputs
            task_completed = False
            verified = False
            outcomes = []
            facts = ()
            missing = list(managed.plan.route.missing_inputs)
            assembly = None
        else:
            board = managed.board
            if board is None:
                return Failed(
                    "target_result_missing", False, str(identity.invocation_key),
                    "Target runtime produced no result board",
                )
            if any(
                item.status is AgentResultStatus.RECONCILING
                for item in board.results
            ):
                return Reconciling(
                    str(identity.workflow_run_id),
                    {
                        "execution": "RECONCILING",
                        "approval_id": managed.deterministic.signal_id,
                        "next_request": "submit a new request_id with the same approval_id",
                    },
                    1.0,
                )
            assembly = turn_result.assembled
            if assembly is None:
                return Failed(
                    "target_response_missing", False,
                    str(identity.invocation_key),
                    "Target turn completed without an assembled response",
                )
            response_text = assembly.text
            verifier_status = assembly.verification_status
            coverage_complete = board.coverage_complete
            task_completed = board.task_completed
            verified = assembly.verified
            outcomes = [
                {
                    "work_item_id": item.work_item_id,
                    "owner_agent": item.owner_agent,
                    "status": item.status.value,
                    "reason_code": item.reason_code,
                }
                for item in board.results
            ]
            facts = board.facts
            missing = list(board.missing_requirement_ids)
            handoff_receipt = next((
                receipt
                for result in board.results
                for receipt in result.action_receipts
                if receipt.requirement_id == "support.handoff_action"
                and receipt.effect_status == "COMMITTED"
            ), None)

        evidence_sha = hashlib.sha256(json.dumps(
            [
                (fact.requirement_id, fact.source_ref, fact.producer_version)
                for fact in facts
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        route = managed.plan.route
        work_items = managed.plan.work.items if managed.plan.work else ()
        receipt_refs = tuple(
            receipt.receipt_id
            for result in (managed.board.results if managed.board else ())
            for receipt in result.action_receipts
            if receipt.effect_status == "COMMITTED"
        )
        elapsed_ms = (time.monotonic() - started) * 1000
        evaluation_trace = {
            "schema_version": "target-evaluation-trace-v1",
            "trigger": {
                "invocation_admitted": True,
                "deterministic_resolution": managed.deterministic.kind.value,
            },
            "artifact": {
                "plan_id": managed.plan.plan_id,
                "registry_fingerprint": managed.plan.registry_fingerprint,
                "route_mode": route.mode.value,
                "owner_ids": list(route.owner_ids),
                "understanding_reason": route.reason_code,
            },
            "consumption": {
                "work_items": [{
                    "work_item_id": item.work_item_id,
                    "control_mode": item.control_mode.value,
                    "owner_agent": item.owner_agent,
                    "allowed_tools": list(item.allowed_tools),
                    "allowed_skills": list(item.allowed_skills),
                } for item in work_items],
                "result_statuses": [item["status"] for item in outcomes],
                "facts": [{
                    "requirement_id": fact.requirement_id,
                    "source_kind": fact.source_kind.value,
                    "source_ref": fact.source_ref,
                    "producer_id": fact.producer_id,
                    "producer_version": fact.producer_version,
                } for fact in facts],
            },
            "state_side_effect": {
                "state_version_before": managed.state_before.version,
                "state_version_after": managed.state_after.version,
                "workstream_statuses": [
                    item.status.value for item in managed.state_after.workstreams
                ],
                "committed_receipt_refs": list(receipt_refs),
            },
            "outcome": {
                "kind": "COMPLETED",
                "verifier_status": verifier_status,
                "task_completed": task_completed,
                "coverage_complete": coverage_complete,
                "missing_requirement_ids": missing,
            },
            "cost": {
                "conversation_planner_invoked": (
                    route.reason_code == "CONVERSATION_AGENT_PLAN"
                ),
                "work_item_count": len(work_items),
                "latency_ms": elapsed_ms,
            },
        }
        public_response = {
            "request_id": str(identity.request_id),
            "conv_id": str(identity.conversation_id),
            "response": response_text,
            "intent": route.reason_code,
            "intent_group": route.owner_ids[0] if route.owner_ids else "other",
            "agent_type": route.owner_ids[0] if route.owner_ids else "general",
            "agent_types": list(route.owner_ids),
            "primary_agent": route.owner_ids[0] if route.owner_ids else "",
            "supporting_agents": list(route.owner_ids[1:]),
            "routing_reason": route.reason_code,
            "routing_disposition": route.mode.value.lower(),
            "synthesis_status": (
                assembly.mode.value.lower() if assembly is not None else "terminal"
            ),
            "synthesis_reason": (
                assembly.verification_reason
                if assembly is not None else "terminal route response"
            ),
            "synthesis_conflicts": list(getattr(managed.board, "conflict_keys", ()) or ()),
            "agent_outcomes": outcomes,
            "task_plan": {
                "plan_id": managed.plan.plan_id,
                "work_item_ids": (
                    [item.work_item_id for item in managed.plan.work.items]
                    if managed.plan.work else []
                ),
            },
            "coverage": {
                "complete": coverage_complete,
                "missing_requirement_ids": missing,
            },
            "escalated": handoff_receipt is not None,
            "latency_ms": elapsed_ms,
            "evaluation_trace": evaluation_trace,
            "verification_status": verifier_status.lower(),
            "task_completed": task_completed,
            "verified": verified,
            "grounded": verified,
            "verification_reason_code": (
                assembly.verification_reason if assembly else "TERMINAL_RESPONSE_NOT_CHECKED"
            ),
            "bundle_version": self._bundle_version,
            "ticket_id": (
                handoff_receipt.receipt_id
                if handoff_receipt is not None else None
            ),
            "ticket_status": (
                "open" if handoff_receipt is not None else None
            ),
            "handoff_created": bool(
                handoff_receipt is not None
            ),
        }
        if assembly is not None and (
            assembly.verification_status == "PASS"
            or assembly.composer_used or assembly.mode is ResponseAssemblyMode.PASS_THROUGH
            or assembly.verified_text_sha256 or assembly.verification_reason in
            ("ANSWER_SUPPORT_CHECKED", "KNOWLEDGE_SUPPORT_CHECKED")
        ):
            if not assembly.verified_text_sha256 or hashlib.sha256(response_text.encode()).hexdigest() != assembly.verified_text_sha256:
                return Failed("target_verified_answer_changed", False,
                              str(identity.invocation_key),
                              "Final answer changed after evidence verification")
        published = self._publication.publish(
            identity,
            response_text=response_text,
            public_response=public_response,
            bundle_version=self._bundle_version,
            evidence_sha256=evidence_sha,
            verifier_status=verifier_status,
            execution_stages=assembly.diagnostics if assembly else (),
            expected_work_controls=tuple(
                item.control for item in work_items if item.control is not None
            ),
        )
        public_response.update({
            "response_id": published.response_id,
            "response_seq": published.response_seq,
            "delivery_status": published.delivery_status,
        })
        return Completed(published.response_id, public_response, stages=assembly.diagnostics if assembly else ())


def _terminal_response(reason_code: str, *, locale="zh-CN") -> str:
    if locale == "en":
        return {
            "WORK_CANCELLED": "The current task has been stopped.",
            "WORKSTREAM_CANCELLED": "The current task has been cancelled.",
            "APPROVAL_DECLINED": "The action was cancelled. No business write was performed this turn.",
            "APPROVAL_EXPIRED": "The confirmation expired and the action was not executed. Please request it again if still needed.",
            "ORDER_ID_REQUIRED": "Please provide the order number.",
            "PRODUCT_MEDIA_REQUIRED": "Please upload a clear image showing the product model or label.",
            "NEW_ADDRESS_REQUIRED": "Please provide the complete new shipping address.",
            "SUPPORTED_GOAL_UNCLEAR": "Please clarify what you would like help with.",
        }.get(reason_code, "Please provide the information needed to continue.")
    if reason_code == "WORK_CANCELLED":
        return "已停止当前事项。"
    if reason_code == "WORKSTREAM_CANCELLED":
        return "已取消当前事项。"
    if reason_code == "APPROVAL_DECLINED":
        return "已取消该操作，本次未执行任何业务写入。"
    if reason_code == "APPROVAL_EXPIRED":
        return "操作确认已过期，本次未执行；如仍需要，请重新发起。"
    return {
        "ORDER_ID_REQUIRED": "请提供需要查询的订单号。",
        "PRODUCT_MEDIA_REQUIRED": "请上传包含商品型号或铭牌的清晰图片。",
        "NEW_ADDRESS_REQUIRED": "请提供要修改成的完整收货地址。",
        "SUPPORTED_GOAL_UNCLEAR": "请说明您要处理订单、退款、商品识别还是人工服务。",
    }.get(reason_code, "请补充完成该任务所需的信息。")
