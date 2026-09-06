"""Governed response selection, optional composition, and final candidate checks."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from application.agent_result import AgentResultStatus
from application.composition_output import render_composition


# CJK prose may touch an ID; ASCII identifier characters must not be sliced.
_REFERENCE = re.compile(r"(?<![A-Za-z_\d])[A-Za-z]{1,20}[-_:]?\d{2,128}(?![A-Za-z_\d])")
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "used_claim_ids", tuple(self.used_claim_ids))


class ConversationComposer(Protocol):
    async def compose(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class ResponseAssembler:
    """Choose the cheapest valid response path and verify the final candidate."""

    version = "response-assembler-v2-attributed-composition"

    def __init__(self, composer: ConversationComposer | None = None, *,
                 knowledge_generator=None, knowledge_verifier=None, knowledge_source_validator=None) -> None:
        self._composer = composer
        self._knowledge_generator = knowledge_generator
        self._knowledge_verifier = knowledge_verifier
        self._knowledge_source_validator = knowledge_source_validator

    async def assemble(self, board, *, current_message: str, system_notice: str = "") -> AssembledResponse:
        from application.knowledge_tool_contract import evidence_items, evidence_id, model_evidence
        knowledge_facts = tuple(fact for result in board.results for fact in result.facts
                                if fact.requirement_id == "knowledge.active_source")
        knowledge_failure = any(result.reason_code.startswith("KNOWLEDGE_")
                                and result.status not in _SUCCESS for result in board.results)
        if not knowledge_facts and not knowledge_failure:
            candidate = await self._assemble_candidate(board, current_message=current_message)
            if candidate.composer_used:
                try:
                    if self._knowledge_verifier is None:
                        raise ValueError("answer support verifier unavailable")
                    verdict = await self._verify_support(board, current_message, candidate.text)
                    if not verdict.publishable or not verdict.grounded:
                        raise ValueError("composed business answer lacks support")
                    return AssembledResponse(system_notice + candidate.text, candidate.mode,
                        candidate.used_claim_ids, True, "PASS", "ANSWER_SUPPORT_CHECKED")
                except Exception:
                    return AssembledResponse(system_notice + _render_board(board), ResponseAssemblyMode.TEMPLATE,
                        (), False, "PASS", "ANSWER_SAFE_FALLBACK")
            from dataclasses import replace
            return replace(candidate, text=system_notice + candidate.text)
        if not knowledge_facts:
            unavailable = any(result.reason_code == "KNOWLEDGE_UNAVAILABLE" for result in board.results)
            return self._knowledge_fallback(board, system_notice, unavailable=unavailable)
        try:
            packs = [json.loads(fact.value_json) for fact in knowledge_facts]
            items = {}
            for pack in packs:
                for item in evidence_items(pack):
                    prior = items.setdefault(item['chunk_id'], item)
                    if prior != item:
                        raise ValueError("conflicting evidence identity")
            # Direct retrieval has no Agent-authored answer. Reuse the existing
            # grounded generator only for this path, never inside knowledge_search.
            direct = all(result.producer_version == "target-tool-executor-v1" for result in board.results)
            # Pure knowledge generation can represent only successful evidence
            # results. A business failure has no facts but still needs an outcome
            # in the answer; receipts and unresolved requirements do too.
            only_knowledge = (
                not board.missing_requirement_ids and not board.conflict_keys
                and all(
                    result.status is AgentResultStatus.SUCCEEDED
                    and bool(result.facts) and not result.action_receipts
                    and all(fact.requirement_id == "knowledge.active_source"
                            for fact in result.facts)
                    for result in board.results
                )
            )
            if direct and only_knowledge:
                if self._knowledge_generator is None:
                    return self._knowledge_fallback(board, system_notice, unavailable=True)
                from mcp.context_packer import ContextCandidate
                contexts = tuple(ContextCandidate(
                    item['chunk_id'], item['source_ref']['source_id'], item['text'],
                    item['source_ref']['start_char'], item['source_ref']['end_char'],
                    source_revision=item['source_ref']['source_revision'],
                    source_checksum=item['source_ref']['checksum'], title=item.get('title', ''),
                    applicability=tuple(sorted(item['source_ref'].get('applicability', {}).items())),
                ) for item in items.values())
                query = packs[0]['evidence_pack']['query'] if len(packs) == 1 else current_message
                generated = await self._knowledge_generator.generate(query, contexts)
                if generated.abstained:
                    return self._knowledge_fallback(board, system_notice)
                if not generated.claims or any(not claim.citations or set(claim.citations) - set(items)
                                               for claim in generated.claims):
                    raise ValueError("generated claims lack supplied evidence")
                text = '\n'.join(claim.text + ' ' + ' '.join(
                    '[' + evidence_id(cid) + ']' for cid in claim.citations
                ) for claim in generated.claims)
                candidate = AssembledResponse(text, ResponseAssemblyMode.PASS_THROUGH, (), True, "PENDING", "KNOWLEDGE_DRAFT")
            else:
                candidate = await self._assemble_candidate(board, current_message=current_message)
            allowed = {evidence_id(cid) for cid in items}
            cited = set(re.findall(r"\[(E[a-zA-Z0-9]+)\]", candidate.text))
            if not cited or cited - allowed or self._knowledge_verifier is None:
                return self._knowledge_fallback(board, system_notice, unavailable=self._knowledge_verifier is None)
            # Check original evidence/facts, never use candidate summaries as evidence.
            verdict = await self._verify_support(
                board, current_message, candidate.text,
                knowledge_evidence={"packs": [model_evidence(pack) for pack in packs], "allowed_evidence_ids": sorted(allowed)},
            )
            if not verdict.publishable or not verdict.grounded:
                return self._knowledge_fallback(board, system_notice)
            if self._knowledge_source_validator is None or not self._knowledge_source_validator(packs):
                return self._knowledge_fallback(board, system_notice, unavailable=True)
            return AssembledResponse(system_notice + candidate.text, candidate.mode,
                                     tuple(sorted(cited)), candidate.composer_used,
                                     "PASS", "KNOWLEDGE_SUPPORT_CHECKED")
        except Exception:
            return self._knowledge_fallback(board, system_notice, unavailable=True)

    async def _verify_support(self, board, message, text, *, knowledge_evidence=None):
        # The injected verifier is shared by business and knowledge composition.
        # Original facts, receipts and outcomes are evidence; generated summaries
        # are not promoted into independent proof of their own wording.
        facts = [json.loads(fact.value_json) for result in board.results for fact in result.facts
                 if fact.requirement_id != "knowledge.active_source"]
        receipts = [claim.value for claim in _allowed_claims(board) if claim.kind == 'RECEIPT']
        return await self._knowledge_verifier.verify(
            message, text, context=json.dumps({'facts': facts, 'receipts': receipts}, ensure_ascii=False),
            knowledge_evidence=knowledge_evidence,
            agent_outcomes=[{"status": result.status.value, "reason": result.reason_code}
                            for result in board.results],
        )

    @staticmethod
    def _knowledge_fallback(board, notice: str, *, unavailable: bool = False) -> AssembledResponse:
        text = ("知识查询或核验服务暂时不可用，请稍后重试或联系人工客服。" if unavailable else
                "现有资料不足以支持可靠结论，请补充适用条件或联系人工客服核实。")
        # A knowledge publication failure only removes knowledge-dependent claims.
        # Committed effects and independently verified state retain their authority.
        from dataclasses import replace
        from types import SimpleNamespace
        from application.agent_result import FactSourceKind
        independent = []
        for result in board.results:
            dependent = result.reason_code.startswith("KNOWLEDGE_") or any(
                fact.requirement_id == "knowledge.active_source" for fact in result.facts)
            if not dependent:
                independent.append(result)
                continue
            facts = tuple(fact for fact in result.facts
                          if fact.source_kind is FactSourceKind.VERIFIED_STATE)
            if facts or result.action_receipts:
                independent.append(replace(result, facts=facts, candidate_response=None,
                                           status=AgentResultStatus.PARTIAL, retryable=False))
        prefix = _render_board(SimpleNamespace(results=independent)) + "\n" if independent else ""
        return AssembledResponse(notice + prefix + text, ResponseAssemblyMode.TEMPLATE, (), False,
                                 "PASS", "KNOWLEDGE_SAFE_ABSTENTION")

    async def _assemble_candidate(
        self, board, *, current_message: str, system_notice: str = "",
    ) -> AssembledResponse:
        claims = _allowed_claims(board)
        mode = self._select_mode(board)
        if mode is ResponseAssemblyMode.PASS_THROUGH:
            result = board.results[0]
            return AssembledResponse(
                system_notice + _candidate_text(result), mode,
                tuple(claim.claim_id for claim in claims), False,
                "PASS", "SINGLE_VERIFIED_RESULT",
            )
        fallback = _render_board(board)
        if mode is ResponseAssemblyMode.TEMPLATE or self._composer is None:
            return AssembledResponse(
                system_notice + fallback, ResponseAssemblyMode.TEMPLATE,
                tuple(claim.claim_id for claim in claims), False,
                "PASS", "DETERMINISTIC_ASSEMBLY",
            )
        payload = {
            "schema_version": "conversation-compose-request-v2-segments",
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
            text, used = render_composition(raw, claims)
            text, used = self.prepare_composed_response(
                text, used, claims, current_message, payload["work_item_outcomes"],
            )
        except Exception:
            return AssembledResponse(
                system_notice + fallback, ResponseAssemblyMode.TEMPLATE,
                tuple(claim.claim_id for claim in claims), False,
                "PASS", "COMPOSER_FALLBACK",
            )
        return AssembledResponse(
            system_notice + text, mode, used, True,
            "PASS", "COMPOSED_FROM_ALLOWED_CLAIMS",
        )

    @staticmethod
    def prepare_composed_response(text, used, claims, current_message, outcomes):
        """Render internal attribution and authoritative outcomes before support checking."""
        by_id = {claim.claim_id: claim for claim in claims}
        known = set(by_id)
        if set(used) - known:
            raise ValueError("composer referenced an unknown claim")
        # Internal provenance stays in used_claim_ids; only evidence IDs are
        # customer citations. Remove exact, known bracketed attribution markers.
        for marker in re.findall(r"\[(?:fact|outcome|receipt):[^\]\n]+\]", text):
            if marker[1:-1] not in known or marker[1:-1] not in used:
                raise ValueError("composer exposed an unknown internal citation")
            text = text.replace(marker, "")
        if any(claim_id in text for claim_id in known):
            raise ValueError("composer exposed an internal claim identifier")
        text = text.strip()
        ResponseAssembler._verify_composed(text, used, claims, current_message)
        notices, attributed = [], list(used)
        for outcome in outcomes:
            status = AgentResultStatus(outcome["status"])
            if status is AgentResultStatus.SUCCEEDED:
                continue
            claim_id = "outcome:" + outcome["work_item_id"]
            if claim_id not in known:
                raise ValueError("outcome notice lacks an authoritative claim")
            claim = by_id[claim_id]
            if (claim.kind != "WORK_ITEM_OUTCOME" or not isinstance(claim.value, dict)
                    or claim.value.get("status") != outcome["status"]
                    or claim.value.get("owner_agent") != outcome["owner_agent"]):
                raise ValueError("outcome notice conflicts with its authoritative claim")
            label = _OWNER_LABELS.get(outcome["owner_agent"], "此项请求")
            notice = "部分请求尚未完成。" if status is AgentResultStatus.PARTIAL else _OUTCOME_TEXT[status]
            notices.append(label + "：" + notice)
            if claim_id not in attributed:
                attributed.append(claim_id)
        return "\n".join([*notices, text]), tuple(attributed)

    @staticmethod
    def _select_mode(board) -> ResponseAssemblyMode:
        if len(board.results) == 1:
            result = board.results[0]
            if result.action_receipts:
                return ResponseAssemblyMode.TEMPLATE
            if (
                result.status in _SUCCESS
                and _candidate_text(result)
                and not board.conflict_keys
                and not board.missing_requirement_ids
            ):
                return ResponseAssemblyMode.PASS_THROUGH
            if result.facts:
                return ResponseAssemblyMode.CONVERSATION_COMPOSE
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
                "summary": _candidate_text(result) or None,
            },
            result.evidence_refs,
        ))
        for index, fact in enumerate(result.facts, start=1):
            claims.append(AllowedClaim(
                f"fact:{result.work_item_id}:{index}",
                "KNOWLEDGE_FACT" if fact.requirement_id == "knowledge.active_source" else "FACT",
                _fact_view(fact),
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
        rendered = []
        for receipt in result.action_receipts:
            if receipt.effect_status != "COMMITTED":
                continue
            if receipt.requirement_id == "support.handoff_action":
                rendered.append(f"人工工单已创建，工单号：{receipt.receipt_id}。")
            else:
                rendered.append(f"操作已完成，凭证号：{receipt.receipt_id}。")
        # A fallback cannot re-publish model-authored candidates after a failed
        # support check. Facts, committed effects and typed outcomes survive.
        text = _render_verified_facts(result)
        if text:
            rendered.append(text)
        if result.status not in _SUCCESS:
            label = _OWNER_LABELS.get(result.owner_agent, "此项请求")
            rendered.append(label + "：" + _OUTCOME_TEXT[result.status])
        elif result.status is AgentResultStatus.PARTIAL:
            rendered.append("部分请求尚未完成。")
        if not rendered:
            rendered.append("已取得部分信息，但暂时无法整理为可靠答复。")
        sections.extend(rendered)
    return "\n".join(sections) or "暂时没有可发布的结果。"


_OWNER_LABELS = {
    "order_logistics": "订单与物流查询", "billing_refund": "退款与账单请求",
    "product_technical": "商品咨询", "general": "知识查询",
    "account_security": "账户请求", "human_support": "人工服务请求",
}
_OUTCOME_TEXT = {
    AgentResultStatus.NEEDS_USER_INPUT: "需要补充信息才能继续。",
    AgentResultStatus.NEEDS_EVIDENCE: "仍需取得必要依据，暂时无法确认结果。",
    AgentResultStatus.WAITING_APPROVAL: "正在等待审批，尚未完成。",
    AgentResultStatus.BLOCKED: "目前无法继续处理。",
    AgentResultStatus.RECONCILING: "正在核实处理结果，暂时无法确认是否完成。",
    AgentResultStatus.RETRYABLE_FAILURE: "本次查询或处理失败，请稍后重试。",
    AgentResultStatus.TERMINAL_FAILURE: "本次未能完成，请核实相关信息或联系人工客服。",
    AgentResultStatus.CANCELLED: "本次处理已取消。",
    AgentResultStatus.SUPERSEDED: "已由更新后的请求替代。",
}


def _candidate_text(result) -> str:
    # Existing persisted direct results may contain model-facing serialized tools.
    # This producer never authors a user reply; its facts retain the full payload.
    if result.producer_version == "target-tool-executor-v1":
        return ""
    return str(result.candidate_response or "").strip()


def _render_verified_facts(result) -> str:
    from application.agent_result import FactSourceKind
    from services.customer_operation_views import ORDER_STATUS_LABELS as statuses
    texts = []
    for fact in result.facts:
        if fact.source_kind is not FactSourceKind.VERIFIED_STATE:
            continue
        value = json.loads(fact.value_json)
        if fact.requirement_id == "order.current_state" and isinstance(value, dict):
            order_id, status = value.get("order_id"), value.get("status")
            if isinstance(order_id, str) and _REFERENCE.fullmatch(order_id) and isinstance(status, str) and status in statuses:
                texts.append(f"订单 {order_id} 当前状态为{statuses[status]}。")
    return "\n".join(dict.fromkeys(texts))


def _fact_view(fact):
    value = json.loads(fact.value_json)
    if fact.requirement_id == "knowledge.active_source":
        from application.knowledge_tool_contract import model_evidence
        return model_evidence(value)
    return value
