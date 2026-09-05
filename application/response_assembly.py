"""Governed response selection, optional composition, and final candidate checks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from application.agent_result import AgentResultStatus


_REFERENCE = re.compile(r"\b[A-Za-z]{1,20}[-_:]?\d{2,128}\b")
_SUCCESS = {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL}


class ResponseAssemblyMode(str, Enum):
    TEMPLATE = "TEMPLATE"
    PASS_THROUGH = "PASS_THROUGH"
    CONVERSATION_COMPOSE = "CONVERSATION_COMPOSE"


@dataclass(frozen=True)
class AllowedClaim:
    claim_id: str
    kind: str
    value: object
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class AssembledResponse:
    text: str
    mode: ResponseAssemblyMode
    used_claim_ids: tuple[str, ...]
    composer_used: bool
    verification_status: str
    verification_reason: str


class ConversationComposer(Protocol):
    async def compose(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class ResponseAssembler:
    """Choose the cheapest valid response path and verify the final candidate."""

    version = "response-assembler-v1"

    def __init__(self, composer: ConversationComposer | None = None) -> None:
        self._composer = composer

    async def assemble(self, board, *, current_message: str) -> AssembledResponse:
        claims = _allowed_claims(board)
        mode = self._select_mode(board)
        if mode is ResponseAssemblyMode.PASS_THROUGH:
            result = board.results[0]
            return AssembledResponse(
                str(result.candidate_response).strip(), mode,
                tuple(claim.claim_id for claim in claims), False,
                "PASS", "SINGLE_VERIFIED_RESULT",
            )
        fallback = _render_board(board)
        if mode is ResponseAssemblyMode.TEMPLATE or self._composer is None:
            return AssembledResponse(
                fallback, ResponseAssemblyMode.TEMPLATE,
                tuple(claim.claim_id for claim in claims), False,
                "PASS", "DETERMINISTIC_ASSEMBLY",
            )
        payload = {
            "schema_version": "conversation-compose-request-v1",
            "current_message": current_message,
            "allowed_claims": [
                {
                    "claim_id": claim.claim_id,
                    "kind": claim.kind,
                    "value": claim.value,
                    "source_refs": list(claim.source_refs),
                }
                for claim in claims
            ],
            "work_item_outcomes": [
                {
                    "work_item_id": result.work_item_id,
                    "owner_agent": result.owner_agent,
                    "status": result.status.value,
                    "reason_code": result.reason_code,
                }
                for result in board.results
            ],
            "missing_requirement_ids": list(board.missing_requirement_ids),
            "partial_delivery_allowed": board.partial_delivery_allowed,
        }
        try:
            raw = await self._composer.compose(payload)
            text = str(raw["response"]).strip()
            used = tuple(str(item) for item in raw["used_claim_ids"])
            self._verify_composed(text, used, claims, current_message)
        except Exception:
            return AssembledResponse(
                fallback, ResponseAssemblyMode.TEMPLATE,
                tuple(claim.claim_id for claim in claims), False,
                "PASS", "COMPOSER_FALLBACK",
            )
        return AssembledResponse(
            text, mode, used, True, "PASS", "COMPOSED_FROM_ALLOWED_CLAIMS",
        )

    @staticmethod
    def _select_mode(board) -> ResponseAssemblyMode:
        if len(board.results) == 1:
            result = board.results[0]
            if result.action_receipts:
                return ResponseAssemblyMode.TEMPLATE
            if (
                result.status in _SUCCESS
                and str(result.candidate_response or "").strip()
                and not board.conflict_keys
                and not board.missing_requirement_ids
            ):
                return ResponseAssemblyMode.PASS_THROUGH
            return ResponseAssemblyMode.TEMPLATE
        return ResponseAssemblyMode.CONVERSATION_COMPOSE

    @staticmethod
    def _verify_composed(text, used, claims, current_message) -> None:
        if not text or not used or len(used) != len(set(used)):
            raise ValueError("composed response contract is incomplete")
        claims_by_id = {item.claim_id: item for item in claims}
        if set(used).difference(claims_by_id):
            raise ValueError("composer referenced an unknown claim")
        allowed_references = set(_REFERENCE.findall(current_message))
        for claim_id in used:
            claim = claims_by_id[claim_id]
            allowed_references.update(_REFERENCE.findall(json.dumps(
                claim.value, ensure_ascii=False, sort_keys=True,
            )))
        if set(_REFERENCE.findall(text)).difference(allowed_references):
            raise ValueError("composer introduced an unsupported business reference")


def _allowed_claims(board) -> tuple[AllowedClaim, ...]:
    claims = []
    for result in board.results:
        outcome_id = f"outcome:{result.work_item_id}"
        claims.append(AllowedClaim(
            outcome_id,
            "WORK_ITEM_OUTCOME",
            {
                "owner_agent": result.owner_agent,
                "status": result.status.value,
                "reason_code": result.reason_code,
                "summary": str(result.candidate_response or "").strip() or None,
            },
            result.evidence_refs,
        ))
        for index, fact in enumerate(result.facts, start=1):
            claims.append(AllowedClaim(
                f"fact:{result.work_item_id}:{index}",
                "FACT",
                json.loads(fact.value_json),
                (fact.source_ref,),
            ))
        for receipt in result.action_receipts:
            claims.append(AllowedClaim(
                f"receipt:{result.work_item_id}:{receipt.receipt_id}",
                "RECEIPT",
                {
                    "receipt_id": receipt.receipt_id,
                    "effect_status": receipt.effect_status,
                    "requirement_id": receipt.requirement_id,
                },
                (receipt.receipt_id,),
            ))
    return tuple(claims)


def _render_board(board) -> str:
    sections = []
    for result in board.results:
        if result.status in _SUCCESS:
            handoff = next((
                receipt for receipt in result.action_receipts
                if receipt.requirement_id == "support.handoff_action"
                and receipt.effect_status == "COMMITTED"
            ), None)
            if handoff is not None:
                sections.append(f"人工工单已创建，工单号：{handoff.receipt_id}。")
                continue
            committed = next((
                receipt for receipt in result.action_receipts
                if receipt.effect_status == "COMMITTED"
            ), None)
            if committed is not None:
                sections.append(f"操作已完成，凭证号：{committed.receipt_id}。")
                continue
            text = str(result.candidate_response or "").strip()
            if not text:
                text = json.dumps(
                    [json.loads(fact.value_json) for fact in result.facts],
                    ensure_ascii=False, sort_keys=True,
                )
            sections.append(f"{result.owner_agent}：{text}")
        else:
            sections.append(f"{result.owner_agent}：未完成（{result.reason_code}）")
    return "\n".join(sections) or "暂时没有可发布的结果。"
