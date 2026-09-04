"""Protocol-neutral `/chat` facade for Target Architecture v1."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from application.agent_result import AgentResultStatus
from application.chat_application import (
    ChatCommand,
    ChatOutcome,
    Completed,
    Conflict,
    Failed,
    NeedsInput,
    Reconciling,
    Rejected,
)
from application.deterministic_resolution import (
    DeterministicResolutionError,
    TurnObservations,
)
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import ProposalDisposition
from core.identity import IdentityContractError, IdentityFactory, InvocationIdentity


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
    def completed(
        self,
        identity: InvocationIdentity,
    ) -> Completed | None: ...

    def publish(
        self,
        identity: InvocationIdentity,
        *,
        response_text: str,
        public_response: Mapping[str, object],
        bundle_version: str,
        evidence_sha256: str,
        verifier_status: str,
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
    ) -> None:
        self._manager = manager
        self._admission = admission
        self._publication = publication
        self._bundle_version = bundle_version
        self._identity_factory = identity_factory or IdentityFactory()

    async def handle(self, command: ChatCommand) -> ChatOutcome:
        started = time.monotonic()
        try:
            identity = self._identity_factory.create_invocation(
                tenant_id=command.tenant_id,
                user_id=command.user_id,
                conversation_id=command.conv_id,
                request_id=command.request_id,
                continuation_id=command.continuation_id,
            )
        except IdentityContractError:
            return Rejected("invalid_invocation_identity", "请求身份字段无效")

        admission = self._admission.admit(
            command,
            identity,
            bundle_version=self._bundle_version,
        )
        if admission.status is TargetAdmissionStatus.CONFLICT:
            return Conflict("IDEMPOTENCY_CONFLICT", admission.existing or {})
        if admission.status is TargetAdmissionStatus.EXISTING:
            completed = self._publication.completed(identity)
            if completed is not None:
                return completed

        observations = TurnObservations(
            command.message,
            tuple(
                ("asset_id", asset_id)
                for asset_id in command.asset_ids[:1]
            ),
            approval_decision=command.approval_decision,
            approval_id=command.approval_id,
        )
        try:
            managed = await self._manager.handle(identity, observations)
        except DeterministicResolutionError as exc:
            return Conflict(
                "APPROVAL_SIGNAL_CONFLICT",
                {"reason": str(exc)},
            )
        except Exception as exc:
            return Failed(
                "target_runtime_failed",
                False,
                str(identity.invocation_key),
                f"Target runtime failed closed: {type(exc).__name__}",
            )

        pending = managed.state_after.pending_approval
        if pending is not None and (
            managed.state_before.pending_approval is None
            or managed.state_before.pending_approval.approval_id != pending.approval_id
        ):
            expires_at = pending.expires_at
            published = self._publication.publish_interaction(
                identity,
                signal_id=pending.approval_id,
                signal_version=pending.version,
                challenge="操作前置条件已确认。是否继续执行？",
                resume_schema={
                    "type": "object",
                    "required": ["approval_id", "approved"],
                    "properties": {
                        "approval_id": {"const": pending.approval_id},
                        "approved": {"type": "boolean"},
                    },
                },
                expires_at=expires_at,
            )
            return NeedsInput(
                str(identity.workflow_run_id),
                pending.approval_id,
                "APPROVAL",
                expires_at,
                published.response_id,
            )

        if managed.plan.work is None:
            failure = {
                "SEMANTIC_PROVIDER_FAILURE": (
                    "semantic_provider_unavailable",
                    True,
                    "语义服务暂时不可用，请稍后重试。",
                ),
                "SEMANTIC_PROVIDER_OUTPUT_INVALID": (
                    "semantic_provider_output_invalid",
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
            response_text = _terminal_response(managed.plan.route.reason_code)
            verifier_status = "CLARIFY" if disposition == "CLARIFY" else "PASS"
            outcomes = []
            facts = ()
            missing = list(managed.plan.route.missing_inputs)
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
            response_text = _board_response(board)
            verifier_status = (
                "PASS"
                if not board.missing_requirement_ids and not board.conflict_keys
                else "PARTIAL" if board.partial_delivery_allowed else "UNKNOWN"
            )
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
                "missing_requirement_ids": missing,
            },
            "cost": {
                "semantic_provider_invoked": (
                    route.reason_code == "STRUCTURED_SEMANTIC_ROUTER"
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
            "synthesis_status": "deterministic",
            "synthesis_reason": "verified Target v1 result board",
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
                "complete": verifier_status == "PASS",
                "missing_requirement_ids": missing,
            },
            "escalated": handoff_receipt is not None,
            "latency_ms": elapsed_ms,
            "evaluation_trace": evaluation_trace,
            "verification_status": verifier_status.lower(),
            "verified": verifier_status == "PASS",
            "grounded": verifier_status == "PASS",
            "verification_reason_code": (
                "TARGET_REQUIREMENTS_SATISFIED"
                if verifier_status == "PASS" else "TARGET_RESULT_INCOMPLETE"
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
        published = self._publication.publish(
            identity,
            response_text=response_text,
            public_response=public_response,
            bundle_version=self._bundle_version,
            evidence_sha256=evidence_sha,
            verifier_status=verifier_status,
        )
        public_response.update({
            "response_id": published.response_id,
            "response_seq": published.response_seq,
            "delivery_status": published.delivery_status,
        })
        return Completed(published.response_id, public_response)


def _terminal_response(reason_code: str) -> str:
    if reason_code == "APPROVAL_DECLINED":
        return "已取消退款申请，本次未执行退款操作。"
    if reason_code == "APPROVAL_EXPIRED":
        return "退款确认已过期，本次未执行退款操作；如仍需要退款，请重新发起。"
    return {
        "ORDER_ID_REQUIRED": "请提供需要查询的订单号。",
        "PRODUCT_MEDIA_REQUIRED": "请上传包含商品型号或铭牌的清晰图片。",
        "SUPPORTED_GOAL_UNCLEAR": "请说明您要处理订单、退款、商品识别还是人工服务。",
    }.get(reason_code, "请补充完成该任务所需的信息。")


def _board_response(board) -> str:
    sections = []
    for result in board.results:
        if result.status in {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL}:
            handoff = next((
                receipt for receipt in result.action_receipts
                if receipt.requirement_id == "support.handoff_action"
                and receipt.effect_status == "COMMITTED"
            ), None)
            if handoff is not None:
                sections.append(
                    f"人工工单已创建，工单号：{handoff.receipt_id}。"
                )
                continue
            committed_receipt = next((
                receipt for receipt in result.action_receipts
                if receipt.effect_status == "COMMITTED"
            ), None)
            if committed_receipt is not None:
                sections.append(
                    f"操作已完成，凭证号：{committed_receipt.receipt_id}。"
                )
                continue
            text = str(result.candidate_response or "").strip()
            if not text:
                owned = [
                    json.loads(fact.value_json)
                    for fact in result.facts
                ]
                text = json.dumps(owned, ensure_ascii=False, sort_keys=True)
            sections.append(f"{result.owner_agent}：{text}")
        else:
            sections.append(
                f"{result.owner_agent}：未完成（{result.reason_code}）"
            )
    return "\n".join(sections) or "暂时没有可发布的结果。"
