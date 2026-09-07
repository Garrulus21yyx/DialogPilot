"""Governed response selection, optional composition, and final candidate checks."""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from application.agent_result import AgentResultStatus
from application.composition_output import render_composition, prepare_composition_payload


# CJK prose may touch an ID; ASCII identifier characters must not be sliced.
_REFERENCE = re.compile(r"(?<![A-Za-z_\d])[A-Za-z]{1,20}[-_:]?\d{2,128}(?![A-Za-z_\d])")
_SUCCESS = {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL}


class ResponseAssemblyMode(str, Enum):
    TEMPLATE = "TEMPLATE"
    # Decode historical checkpoints only; no current execution selects this mode.
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
    verified_text_sha256: str = ""
    approval_operation_key: str = ""

    @property
    def verified(self) -> bool:
        """Only a successful check bound to this exact text attests the answer."""
        return self.verification_status == "PASS" and bool(self.verified_text_sha256)

    def __post_init__(self) -> None:
        object.__setattr__(self, "used_claim_ids", tuple(self.used_claim_ids))
        if self.verified_text_sha256 and self.verified_text_sha256 != hashlib.sha256(self.text.encode()).hexdigest():
            raise ValueError("answer text changed after verification")
        if self.approval_operation_key and (not self.verified_text_sha256 or self.verification_status != "PASS"):
            raise ValueError("approval presentation requires a verified answer")


class ConversationComposer(Protocol):
    async def compose(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...


class ResponseAssembler:
    """Choose the cheapest valid response path and verify the final candidate."""

    version = "response-assembler-v7-conversation-owned"

    @staticmethod
    def interaction_prelude(board, *, locale="zh-CN") -> str:
        """Deliver independent committed results alongside a pending question.

        This uses the existing deterministic renderer, not unverified domain
        prose or a second composition call. The underlying board is unchanged.
        """
        from types import SimpleNamespace
        results = tuple(result for result in board.results if result.status in _SUCCESS
                        and (result.facts or result.action_receipts))
        return _render_board(SimpleNamespace(results=results), locale=locale) if results else ""

    def __init__(self, composer: ConversationComposer | None = None, *,
                 knowledge_verifier=None, knowledge_source_validator=None,
                 fallback_locale="zh-CN", internal_tool_names=()) -> None:
        if fallback_locale not in {"zh-CN", "en"}:
            raise ValueError("unsupported customer fallback locale")
        self.fallback_locale = fallback_locale
        self._internal_tool_names = frozenset(internal_tool_names)
        self._composer = composer
        self._knowledge_verifier = knowledge_verifier
        self._knowledge_source_validator = knowledge_source_validator

    async def assemble(self, board, *, current_message: str, system_notice: str = "", conversation_context=None,
                       pending_approval=None, requested_inputs=()) -> AssembledResponse:
        from dataclasses import replace
        response = await self._assemble(board, current_message=current_message,
            system_notice=system_notice, conversation_context=conversation_context, pending_approval=pending_approval,
            requested_inputs=requested_inputs)
        if requested_inputs and not response.verified:
            prelude = self.interaction_prelude(board, locale=self.fallback_locale)
            notice = _message(self.fallback_locale,
                "暂时无法组织后续问题，已保留处理进度，请稍后重试。",
                "I could not prepare the follow-up question. Your progress is saved; please try again later.")
            return AssembledResponse((prelude + "\n" if prelude else "") + notice,
                ResponseAssemblyMode.TEMPLATE, (), False, "NOT_CHECKED", "INTERACTION_UNAVAILABLE")
        pending = [r.pending_action for r in board.results if r.pending_action]
        operation_key = pending_approval.operation_key if pending_approval else pending[0].operation_key if len(pending) == 1 else ""
        if not requested_inputs and operation_key and response.verified_text_sha256:
            response = replace(response, approval_operation_key=operation_key)
        return response

    async def _assemble(self, board, *, current_message: str, system_notice: str = "", conversation_context=None,
                        pending_approval=None, requested_inputs=()) -> AssembledResponse:
        from dataclasses import replace
        from application.knowledge_tool_contract import evidence_items, evidence_id, model_evidence

        knowledge_facts = tuple(fact for result in board.results for fact in result.facts
                                if fact.requirement_id == "knowledge.active_source")
        knowledge_failure = any(result.reason_code.startswith("KNOWLEDGE_")
                                and result.status not in _SUCCESS for result in board.results)
        packs, allowed = [], set()
        try:
            items = {}
            for fact in knowledge_facts:
                pack = json.loads(fact.value_json)
                packs.append(pack)
                for item in evidence_items(pack):
                    prior = items.setdefault(item["chunk_id"], item)
                    if prior != item:
                        raise ValueError("conflicting evidence identity")
            allowed = {evidence_id(cid) for cid in items}
            evidence = ({"packs": [model_evidence(pack) for pack in packs],
                         "allowed_evidence_ids": sorted(allowed)} if packs else None)

            # Retrieval supplies evidence, never a second public-answer author.
            # Failed work remains an outcome alongside independent results/input.
            candidate = await self._assemble_candidate(
                board, current_message=current_message, conversation_context=conversation_context,
                pending_approval=pending_approval, requested_inputs=requested_inputs)
            candidate = replace(candidate, text=system_notice + candidate.text)
            if not candidate.composer_used:
                if knowledge_facts or knowledge_failure:
                    return self._knowledge_fallback(board, system_notice, unavailable=True)
                return candidate
            if self._knowledge_verifier is None:
                raise ValueError("answer support verifier unavailable")
            candidate, verdict = await self._verify_with_revision(
                board, current_message, candidate, conversation_context=conversation_context,
                system_notice=system_notice, pending_approval=pending_approval,
                requested_inputs=requested_inputs, knowledge_evidence=evidence)
            if not candidate.composer_used or not verdict.publishable or not verdict.grounded:
                raise ValueError("composed answer lacks support")
            cited = set(re.findall(r"\[(E[a-zA-Z0-9]+)\]", candidate.text))
            if cited - allowed or (knowledge_facts and not cited):
                raise ValueError("answer citations do not match supplied evidence")
            if packs and (self._knowledge_source_validator is None
                          or not self._knowledge_source_validator(packs)):
                raise ValueError("knowledge sources are no longer valid")
            return AssembledResponse(
                candidate.text, candidate.mode,
                tuple(sorted(cited)) if packs else candidate.used_claim_ids,
                True, "PASS", "KNOWLEDGE_SUPPORT_CHECKED" if packs else "ANSWER_SUPPORT_CHECKED",
                hashlib.sha256(candidate.text.encode()).hexdigest())
        except Exception:
            if knowledge_facts or knowledge_failure:
                return self._knowledge_fallback(board, system_notice, unavailable=True)
            return AssembledResponse(
                system_notice + _render_board(board, locale=self.fallback_locale),
                ResponseAssemblyMode.TEMPLATE, (), False, "NOT_CHECKED", "ANSWER_SAFE_FALLBACK")

    async def _verify_with_revision(self, board, message, candidate, *,
                                    knowledge_evidence=None, conversation_context=None,
                                    system_notice="", pending_approval=None, requested_inputs=()):
        verdict = await self._verify_support(board, message, candidate.text,
            used_claim_ids=candidate.used_claim_ids,
            knowledge_evidence=knowledge_evidence, conversation_context=conversation_context, pending_approval=pending_approval,
            requested_inputs=requested_inputs)
        if verdict.publishable or verdict.assessment is None or self._composer is None:
            return candidate, verdict
        from dataclasses import asdict, replace
        feedback = {
            "previous_answer": candidate.text,
            "assessment": asdict(verdict.assessment),
            "reason_code": verdict.reason_code.value,
            "reason": verdict.reason,
        }
        # Recompose from the same original board only. This performs no business
        # tool execution and has exactly one repair attempt, never recursion.
        revised = await self._assemble_candidate(board, current_message=message,
            conversation_context=conversation_context, repair_feedback=feedback, pending_approval=pending_approval,
            requested_inputs=requested_inputs)
        revised = replace(revised, text=system_notice + revised.text)
        revised_verdict = await self._verify_support(board, message, revised.text,
            used_claim_ids=revised.used_claim_ids,
            knowledge_evidence=knowledge_evidence, conversation_context=conversation_context, pending_approval=pending_approval,
            requested_inputs=requested_inputs)
        return revised, revised_verdict

    async def _verify_support(self, board, message, text, *, used_claim_ids=None, knowledge_evidence=None, conversation_context=None, pending_approval=None, requested_inputs=()):
        # The injected verifier is shared by business and knowledge composition.
        # Original facts, receipts and outcomes are evidence; generated summaries
        # are not promoted into independent proof of their own wording.
        facts = [json.loads(fact.value_json) for result in board.results for fact in result.facts
                 if fact.requirement_id != "knowledge.active_source"]
        receipts = [claim.value for claim in _allowed_claims(board) if claim.kind == 'RECEIPT']
        proposals = [claim.value for claim in _allowed_claims(board, pending_approval, requested_inputs=requested_inputs)
                     if claim.kind == 'PENDING_ACTION']
        missing_outcomes = [result.work_item_id for result in board.results
            if result.status not in {AgentResultStatus.SUCCEEDED, AgentResultStatus.WAITING_APPROVAL}
            and used_claim_ids is not None and 'outcome:' + result.work_item_id not in used_claim_ids]
        invalid_citations = sorted(name for name in self._internal_tool_names if '[' + name + ']' in text)
        input_claims = [claim for claim in _allowed_claims(board, requested_inputs=requested_inputs)
                        if claim.kind == "INPUT_REQUEST"]
        missing_outcomes.extend(claim.claim_id for claim in input_claims
                                if used_claim_ids is not None and claim.claim_id not in used_claim_ids)
        inputs = dict(question=message, answer=text,
            context=json.dumps({'facts': facts, 'receipts': receipts, 'pending_actions': proposals,
                                'unrepresented_outcomes': missing_outcomes,
                                'invalid_citations': invalid_citations,
                                'requested_inputs': [claim.value for claim in input_claims],
                                'user_context': conversation_context}, ensure_ascii=False),
            knowledge_evidence=knowledge_evidence,
            agent_outcomes=[{"status": result.status.value, "reason": result.reason_code,
                             "execution_feedback": _failure_feedback(result)}
                            for result in board.results])
        verdict = await self._knowledge_verifier.verify(
            message, text, context=inputs['context'],
            knowledge_evidence=inputs['knowledge_evidence'], agent_outcomes=inputs['agent_outcomes'])
        if not verdict.matches_request(**inputs):
            raise ValueError("verification does not match final answer and evidence")
        return verdict

    def _knowledge_fallback(self, board, notice: str, *, unavailable: bool = False) -> AssembledResponse:
        text = (_message(self.fallback_locale,
            "知识查询或核验服务暂时不可用，请稍后重试或联系人工客服。",
            "The information or verification service is temporarily unavailable. Please try again later or contact support.") if unavailable else
            _message(self.fallback_locale,
            "现有资料不足以支持可靠结论，请补充适用条件或联系人工客服核实。",
            "The available information does not support a reliable conclusion. Please provide relevant details or contact support."))
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
        prefix = _render_board(SimpleNamespace(results=independent), locale=self.fallback_locale) + "\n" if independent else ""
        return AssembledResponse(notice + prefix + text, ResponseAssemblyMode.TEMPLATE, (), False,
                                 "NOT_CHECKED", "KNOWLEDGE_SAFE_ABSTENTION")

    async def _assemble_candidate(
        self, board, *, current_message: str, system_notice: str = "", conversation_context=None, repair_feedback=None, pending_approval=None, requested_inputs=(),
    ) -> AssembledResponse:
        claims = _allowed_claims(board, pending_approval, requested_inputs=requested_inputs)
        mode = ResponseAssemblyMode.CONVERSATION_COMPOSE if repair_feedback is not None or pending_approval or requested_inputs else self._select_mode(board)
        fallback = _render_board(board, locale=self.fallback_locale)
        if mode is ResponseAssemblyMode.TEMPLATE or self._composer is None:
            return AssembledResponse(
                system_notice + fallback, ResponseAssemblyMode.TEMPLATE,
                tuple(claim.claim_id for claim in claims), False,
                "NOT_CHECKED", "DETERMINISTIC_ASSEMBLY",
            )
        payload = {
            "schema_version": "conversation-compose-request-v3-supports",
            "current_message": current_message,
            "conversation_context": conversation_context,
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
                    "retryable": result.retryable,
                    "execution_feedback": _failure_feedback(result),
                }
                for result in board.results
            ],
            # Working text is context, never support for business claims.
            "domain_notes": [
                {"work_item_id": result.work_item_id, "text": _candidate_text(result)}
                for result in board.results if _candidate_text(result)
            ],
            "missing_requirement_ids": list(board.missing_requirement_ids),
            "partial_delivery_allowed": board.partial_delivery_allowed,
            "response_requirements": [
                "Address the customer directly in the language they use or request. Do not include drafting notes, self-instructions or commentary about how to answer.",
                "Internal tool names, operation keys and raw parameter JSON are not customer explanations. Use supplied facts to explain item references; do not invent names, prices, fees or return instructions.",
                *([_APPROVAL_DESCRIPTION_REQUIREMENT]
                  if any(c.kind == "PENDING_ACTION" for c in claims) else []),
            ],
        }
        if repair_feedback is not None:
            payload["repair_feedback"] = repair_feedback
        try:
            payload = prepare_composition_payload(payload)
            raw = await self._composer.compose(payload)
            text, used = render_composition(raw, claims)
            text, used = self.prepare_composed_response(
                text, used, claims, current_message, payload["work_item_outcomes"],
                conversation_context=conversation_context,
            )
        except Exception:
            return AssembledResponse(
                system_notice + fallback, ResponseAssemblyMode.TEMPLATE,
                tuple(claim.claim_id for claim in claims), False,
                "NOT_CHECKED", "COMPOSER_FALLBACK",
            )
        return AssembledResponse(
            system_notice + text, mode, used, True,
            "PENDING", "COMPOSED_FROM_ALLOWED_CLAIMS",
        )

    @staticmethod
    def prepare_composed_response(text, used, claims, current_message, outcomes, *, conversation_context=None):
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
        ResponseAssembler._verify_composed(text, used, claims, current_message, conversation_context=conversation_context)
        for outcome in outcomes:
            status = AgentResultStatus(outcome["status"])
            if status in {AgentResultStatus.SUCCEEDED, AgentResultStatus.WAITING_APPROVAL}:
                continue
            claim_id = "outcome:" + outcome["work_item_id"]
            if claim_id not in known:
                raise ValueError("outcome notice lacks an authoritative claim")
            claim = by_id[claim_id]
            if (claim.kind != "WORK_ITEM_OUTCOME" or not isinstance(claim.value, dict)
                    or claim.value.get("status") != outcome["status"]
                    or claim.value.get("owner_agent") != outcome["owner_agent"]):
                raise ValueError("outcome notice conflicts with its authoritative claim")
        # The author expresses outcomes in the customer's language. Missing
        # outcome support is repair feedback at verification, not server prose
        # prepended to an otherwise complete answer or fabricated attribution.
        return text, tuple(used)

    @staticmethod
    def _select_mode(board) -> ResponseAssemblyMode:
        if any(result.pending_action for result in board.results):
            return ResponseAssemblyMode.CONVERSATION_COMPOSE
        if len(board.results) == 1:
            result = board.results[0]
            if result.action_receipts and not result.facts and not _candidate_text(result):
                return ResponseAssemblyMode.TEMPLATE
            if _candidate_text(result) or result.facts or result.status in {
                AgentResultStatus.BLOCKED, AgentResultStatus.RETRYABLE_FAILURE,
                AgentResultStatus.TERMINAL_FAILURE,
            }:
                return ResponseAssemblyMode.CONVERSATION_COMPOSE
            return ResponseAssemblyMode.TEMPLATE
        return ResponseAssemblyMode.CONVERSATION_COMPOSE

    @staticmethod
    def _verify_composed(text, used, claims, current_message, *, conversation_context=None) -> None:
        if not text or not used or len(used) != len(set(used)):
            raise ValueError("composed response contract is incomplete")
        claims_by_id = {item.claim_id: item for item in claims}
        if set(used).difference(claims_by_id):
            raise ValueError("composer referenced an unknown claim")
        allowed_references = set(_REFERENCE.findall(current_message))
        if conversation_context is not None:
            # Reference occurrence is not authority; the support gate still
            # distinguishes user mentions from verified business state.
            allowed_references.update(_REFERENCE.findall(json.dumps(conversation_context, ensure_ascii=False)))
        for claim_id in used:
            claim = claims_by_id[claim_id]
            allowed_references.update(_REFERENCE.findall(json.dumps(
                claim.value, ensure_ascii=False, sort_keys=True,
            )))
        if set(_REFERENCE.findall(text)).difference(allowed_references):
            raise ValueError("composer introduced an unsupported business reference")


def _failure_feedback(result):
    from application.work_recovery import failure_feedback
    return failure_feedback(result) if result.status not in _SUCCESS else []


def _allowed_claims(board, pending_approval=None, *, requested_inputs=()) -> tuple[AllowedClaim, ...]:
    claims = []
    for spec in requested_inputs:
        claims.append(AllowedClaim(
            f"input:{spec.target_work_item_id}:{spec.field_name}", "INPUT_REQUEST",
            {"target_work_item_id": spec.target_work_item_id, "field_name": spec.field_name,
             "value_schema": spec.value_schema, "question_hint": spec.question_hint}, ()))
    if pending_approval and not requested_inputs:
        claims.append(AllowedClaim("proposal:" + pending_approval.work_item_id, "PENDING_ACTION",
            {"action_ref": pending_approval.action_ref,
             "arguments": {arg.name: arg.value for arg in pending_approval.arguments},
             "effect_status": "NOT_EXECUTED"}, ()))
    for result in board.results:
        if result.pending_action and pending_approval is None and not requested_inputs:
            action = result.pending_action
            claims.append(AllowedClaim(
                f"proposal:{result.work_item_id}", "PENDING_ACTION",
                {"action_ref": action.action_ref, "objective": action.objective,
                 "arguments": {arg.name: arg.value for arg in action.arguments},
                 "effect_status": "NOT_EXECUTED"}, (),
            ))
        from application.agent_result import FactSourceKind
        controlled_refund = any(f.requirement_id == "refund.current_state"
            and f.source_kind is FactSourceKind.VERIFIED_STATE for f in result.facts)
        outcome_id = f"outcome:{result.work_item_id}"
        if not (controlled_refund and result.status is AgentResultStatus.SUCCEEDED):
            claims.append(AllowedClaim(
                outcome_id,
                "WORK_ITEM_OUTCOME",
                {
                    "owner_agent": result.owner_agent,
                    "status": result.status.value,
                    "reason_code": result.reason_code,
                    "summary": None,
                },
                result.evidence_refs,
            ))
        for index, fact in enumerate(result.facts, start=1):
            claims.append(AllowedClaim(
                f"fact:{result.work_item_id}:{index}",
                "KNOWLEDGE_FACT" if fact.requirement_id == "knowledge.active_source" else
                "CONTROLLED_REFUND_FACT" if fact.requirement_id == "refund.current_state"
                and fact.source_kind is FactSourceKind.VERIFIED_STATE else "FACT",
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


_APPROVAL_DESCRIPTION_REQUIREMENT = (
    "Approval description: explain the pending action's target, material changes and payment terms, "
    "state that it has not executed, and ask for approval."
)


def _message(locale, chinese, english):
    return english if locale == "en" else chinese


def _render_board(board, *, locale="zh-CN") -> str:
    sections = []
    for result in board.results:
        rendered = []
        for receipt in result.action_receipts:
            if receipt.effect_status != "COMMITTED":
                continue
            if receipt.requirement_id == "support.handoff_action":
                rendered.append(_message(locale, f"人工工单已创建，工单号：{receipt.receipt_id}。",
                    f"A support ticket has been created. Ticket number: {receipt.receipt_id}."))
            else:
                rendered.append(_message(locale, "请求已提交。", "The request has been submitted."))
        # A fallback cannot re-publish model-authored candidates after a failed
        # support check. Facts, committed effects and typed outcomes survive.
        text = _render_verified_facts(result, locale=locale)
        if text:
            rendered.append(text)
        if result.status not in _SUCCESS:
            label = (_OWNER_LABELS_EN.get(result.owner_agent, "This request") if locale == "en"
                     else _OWNER_LABELS.get(result.owner_agent, "此项请求"))
            rendered.append(label + (": " if locale == "en" else "：")
                + (_OUTCOME_TEXT_EN if locale == "en" else _OUTCOME_TEXT)[result.status])
        elif result.status is AgentResultStatus.PARTIAL:
            rendered.append(_message(locale, "部分请求尚未完成。", "Part of the request remains incomplete."))
        if not rendered:
            rendered.append(_message(locale, "暂时无法提供可靠答复，请稍后重试或联系人工客服。",
                "I cannot provide a reliable answer right now. Please try again later or contact support."))
        sections.extend(rendered)
    return "\n".join(sections) or _message(locale, "暂时没有可发布的结果。", "No result is available yet.")


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

_OWNER_LABELS_EN = {
    "order_logistics": "Order and shipping", "billing_refund": "Refund and billing",
    "product_technical": "Product enquiry", "general": "Information enquiry",
    "account_security": "Account request", "human_support": "Support request",
}
_OUTCOME_TEXT_EN = {
    AgentResultStatus.NEEDS_USER_INPUT: "More information is needed to continue.",
    AgentResultStatus.NEEDS_EVIDENCE: "More evidence is needed; the result cannot yet be confirmed.",
    AgentResultStatus.WAITING_APPROVAL: "Awaiting approval; the action has not been completed.",
    AgentResultStatus.BLOCKED: "This cannot proceed at present.",
    AgentResultStatus.RECONCILING: "The outcome is being checked; completion is not yet confirmed.",
    AgentResultStatus.RETRYABLE_FAILURE: "The attempt failed. Please try again later.",
    AgentResultStatus.TERMINAL_FAILURE: "This could not be completed. Please check the details or contact support.",
    AgentResultStatus.CANCELLED: "Processing was cancelled.",
    AgentResultStatus.SUPERSEDED: "This has been replaced by your updated request.",
}


def _candidate_text(result) -> str:
    # Existing persisted direct results may contain model-facing serialized tools.
    # This producer never authors a user reply; its facts retain the full payload.
    if result.producer_version == "target-tool-executor-v1":
        return ""
    return str(result.candidate_response or "").strip()


def _render_verified_facts(result, *, locale="zh-CN") -> str:
    from application.agent_result import FactSourceKind
    from services.customer_operation_views import ORDER_STATUS_LABELS as statuses
    texts = []
    for fact in result.facts:
        if fact.source_kind is not FactSourceKind.VERIFIED_STATE:
            continue
        value = json.loads(fact.value_json)
        if fact.requirement_id == "refund.current_state":
            from services.customer_operation_views import refund_lookup_statements, UnsupportedRefundObservation
            try:
                texts.extend(text for _, text in refund_lookup_statements(value, locale=locale))
            except UnsupportedRefundObservation:
                texts.append(_message(locale, "当前退款查询结果格式无法确认，请重新查询。",
                    "The refund lookup returned an unrecognized result. Please query it again."))
        if fact.requirement_id == "order.current_state" and isinstance(value, dict):
            order_id, status = value.get("order_id"), value.get("status")
            if isinstance(order_id, str) and _REFERENCE.fullmatch(order_id) and isinstance(status, str) and status in statuses:
                texts.append(_message(locale, f"订单 {order_id} 当前状态为{statuses[status]}。",
                    f"The current recorded status of order {order_id} is {status}."))
    return "\n".join(dict.fromkeys(texts))


def _fact_view(fact):
    value = json.loads(fact.value_json)
    if fact.requirement_id == "knowledge.active_source":
        from application.knowledge_tool_contract import model_evidence
        return model_evidence(value)
    return value
