"""Typed handoff draft, commit policy, ticket adapter, and publication selection."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from application.agent_result import AgentResult, AgentResultStatus, FactRecord, ReceiptRef
from application.conversation_state import ConversationOwner, ConversationState
from application.publication import (
    FinalResponseCommand,
    HumanReplyCommand,
    ProjectionDisposition,
    PublicationPolicy,
)
from application.work_item import WorkItem
from application.write_workflow import WriteOutcomeStatus, WriteToolOutcome
from core.identity import InvocationIdentity
from services.ticket_service import TicketPriority, TicketService


class HandoffContractError(ValueError):
    pass


@dataclass(frozen=True)
class HandoffDraft:
    handoff_id: str
    target_queue: str
    user_goal: str
    problem_summary: str
    reason_codes: tuple[str, ...]
    verified_facts: tuple[FactRecord, ...]
    user_assertions: tuple[str, ...]
    action_receipts: tuple[ReceiptRef, ...]
    missing_materials: tuple[str, ...]
    media_evidence_refs: tuple[str, ...]
    risk: str
    commitments_and_sla: tuple[str, ...]
    recommended_next_action: str
    explicit_user_request: bool = False
    actions_attempted: tuple[str, ...] = ()
    schema_version: str = "handoff-draft-v1"

    def __post_init__(self) -> None:
        required = (
            self.handoff_id,
            self.target_queue,
            self.user_goal,
            self.problem_summary,
            self.risk,
            self.recommended_next_action,
            self.schema_version,
        )
        if any(not str(value or "").strip() for value in required):
            raise HandoffContractError("handoff draft is incomplete")
        if not self.reason_codes or any(not item.strip() for item in self.reason_codes):
            raise HandoffContractError("handoff requires reason codes")
        if self.risk not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise HandoffContractError("handoff risk is unsupported")

    def to_payload(self) -> dict[str, object]:
        """Serialize the reviewed handoff contract without losing provenance."""
        return {
            "schema_version": self.schema_version,
            "handoff_id": self.handoff_id,
            "target_queue": self.target_queue,
            "user_goal": self.user_goal,
            "problem_summary": self.problem_summary,
            "reason_codes": list(self.reason_codes),
            "verified_facts": [
                {
                    "subject_ref": fact.subject_ref,
                    "requirement_id": fact.requirement_id,
                    "value_json": fact.value_json,
                    "source_kind": fact.source_kind.value,
                    "source_ref": fact.source_ref,
                    "producer_id": fact.producer_id,
                    "producer_version": fact.producer_version,
                    "observed_at": fact.observed_at.isoformat(),
                }
                for fact in self.verified_facts
            ],
            "user_assertions": list(self.user_assertions),
            "action_receipts": [
                {
                    "receipt_id": receipt.receipt_id,
                    "schema_version": receipt.schema_version,
                    "operation_key": receipt.operation_key,
                    "effect_status": receipt.effect_status,
                    "requirement_id": receipt.requirement_id,
                }
                for receipt in self.action_receipts
            ],
            "actions_attempted": list(self.actions_attempted),
            "missing_materials": list(self.missing_materials),
            "media_evidence_refs": list(self.media_evidence_refs),
            "risk": self.risk,
            "commitments_and_sla": list(self.commitments_and_sla),
            "recommended_next_action": self.recommended_next_action,
            "explicit_user_request": self.explicit_user_request,
        }


@dataclass(frozen=True)
class AcceptedHandoff:
    draft: HandoffDraft
    policy_version: str
    reason_code: str


class HandoffCommitPolicy:
    version = "handoff-commit-policy-v1"
    _SYSTEM_REASONS = frozenset({
        "SAFETY_FAIL_CLOSED",
        "CAPABILITY_UNSUPPORTED",
        "EVIDENCE_CONFLICT",
        "REPEATED_TOOL_FAILURE",
        "COMPLIANCE_REQUIRED",
    })

    def accept(self, draft: HandoffDraft) -> AcceptedHandoff:
        if draft.explicit_user_request:
            return AcceptedHandoff(draft, self.version, "EXPLICIT_USER_HANDOFF")
        if not self._SYSTEM_REASONS.intersection(draft.reason_codes):
            raise HandoffContractError("handoff has no registered commit reason")
        return AcceptedHandoff(draft, self.version, "POLICY_HANDOFF")


class TicketServiceHandoffToolPort:
    """Use the existing TicketService as the sole handoff business owner."""

    def __init__(
        self,
        service: TicketService,
        invocation: InvocationIdentity,
        accepted: AcceptedHandoff,
    ) -> None:
        self._service = service
        self._invocation = invocation
        self._accepted = accepted

    async def execute(
        self,
        item: WorkItem,
        *,
        tool_id: str,
        arguments: Mapping[str, object],
        operation_key: str,
    ) -> WriteToolOutcome:
        if tool_id != "support_ticket_create":
            raise HandoffContractError("handoff adapter accepts only support_ticket_create")
        if operation_key != item.operation_key:
            raise HandoffContractError("handoff operation key changed")
        draft = self._accepted.draft
        ticket, _created = await asyncio.to_thread(
            self._service.create_ticket,
            idempotency_key=operation_key,
            user_id=str(self._invocation.user_id),
            conv_id=str(self._invocation.conversation_id),
            request_id=str(self._invocation.request_id),
            question=draft.problem_summary,
            published_response="",
            reason=(
                f"{self._accepted.reason_code}: "
                + ",".join(draft.reason_codes)
            ),
            priority=_priority(draft.risk),
            agent_type="human_service",
            intent="human_handoff",
            verification_status="handoff_policy_accepted",
            identity_metadata={
                **self._invocation.metadata(),
                "handoff_id": draft.handoff_id,
                "target_queue": draft.target_queue,
                "policy_version": self._accepted.policy_version,
            },
        )
        return WriteToolOutcome(
            WriteOutcomeStatus.COMMITTED,
            ticket.ticket_id,
            "ticket-receipt-v1",
            "HANDOFF_TICKET_COMMITTED",
        )


class HandoffPublicationSelector:
    """Select user-visible wording from a receipt, never from a draft claim."""

    version = "handoff-publication-selector-v1"

    def select(
        self,
        *,
        invocation: InvocationIdentity,
        result: AgentResult,
        bundle_version: str,
        created_at: str,
        policy: PublicationPolicy,
        state: ConversationState,
    ) -> FinalResponseCommand:
        receipt = next((
            item for item in result.action_receipts
            if item.requirement_id == "support.handoff_action"
            and item.effect_status == "COMMITTED"
        ), None)
        committed = (
            result.status is AgentResultStatus.SUCCEEDED and receipt is not None
        )
        if committed:
            response = f"人工工单已创建，工单号：{receipt.receipt_id}。"
            verifier_status = "PASS"
            disposition = ProjectionDisposition.NORMAL
        else:
            response = "尚未确认人工工单创建成功，请稍后使用相同请求重试或直接联系人工客服。"
            verifier_status = "UNKNOWN"
            disposition = ProjectionDisposition.CLARIFICATION
        evidence = hashlib.sha256(json.dumps(
            [item.receipt_id for item in result.action_receipts],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return FinalResponseCommand(
            invocation.invocation_key,
            str(invocation.tenant_id),
            str(invocation.user_id),
            str(invocation.conversation_id),
            response,
            f"handoff:{invocation.invocation_key}",
            self.version,
            verifier_status,
            {
                "reason_code": result.reason_code,
                "ticket_receipt": receipt.receipt_id if receipt else "",
            },
            evidence,
            bundle_version,
            "",
            created_at,
            policy,
            disposition,
            expected_state_fingerprint=state.fingerprint,
        )

    def select_human_reply(
        self,
        *,
        state: ConversationState,
        ticket_id: str,
        handoff_id: str,
        human_message_id: str,
        text: str,
        created_at: str,
        policy: PublicationPolicy,
    ) -> HumanReplyCommand:
        if state.owner is not ConversationOwner.HUMAN:
            raise HandoffContractError("automation-owned conversation rejects human reply")
        if state.human_ticket_ref != ticket_id:
            raise HandoffContractError("human reply ticket differs from conversation owner")
        if any(not str(value or "").strip() for value in (
            handoff_id, human_message_id, text, created_at,
        )):
            raise HandoffContractError("human reply is incomplete")
        return HumanReplyCommand(
            str(state.tenant_id),
            str(state.user_id),
            str(state.conversation_id),
            ticket_id,
            handoff_id,
            human_message_id,
            text,
            created_at,
            policy,
        )


def _priority(risk: str) -> TicketPriority:
    if risk == "CRITICAL":
        return TicketPriority.CRITICAL
    if risk == "HIGH":
        return TicketPriority.HIGH
    return TicketPriority.NORMAL
