"""Governed response selection, optional composition, and final candidate checks."""
from __future__ import annotations

import json
import hashlib
import re
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Protocol

from application.agent_result import AgentResultStatus
from application.chat_contracts import StageObservation, StageStatus
from application.result_board import current_facts


# CJK prose may touch an ID; ASCII identifier characters must not be sliced.
_REFERENCE = re.compile(r"(?<![A-Za-z_\d])[A-Za-z]{1,20}[-_:]?\d{2,128}(?![A-Za-z_\d])")
_SUCCESS = {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL}
logger = logging.getLogger(__name__)


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
    evidence_refs: tuple[str, ...]
    composer_used: bool
    verification_status: str
    verification_reason: str
    verified_text_sha256: str = ""
    approval_operation_key: str = ""
    retryable: bool = False
    diagnostics: tuple[StageObservation, ...] = ()
    evidence_sha256: str = ""
    evidence_json: str = ""

    @property
    def verified(self) -> bool:
        """Only a successful check bound to this exact text attests the answer."""
        return self.verification_status == "PASS" and bool(self.verified_text_sha256)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if self.evidence_json and hashlib.sha256(self.evidence_json.encode()).hexdigest() != self.evidence_sha256:
            raise ValueError("response evidence changed after capture")
        if self.verified_text_sha256 and self.verified_text_sha256 != hashlib.sha256(self.text.encode()).hexdigest():
            raise ValueError("answer text changed after verification")
        if self.approval_operation_key and (not self.verified_text_sha256 or self.verification_status != "PASS"):
            raise ValueError("approval presentation requires a verified answer")


class ConversationComposer(Protocol):
    async def compose(self, payload: Mapping[str, object]) -> str: ...


class ResponseAssembler:
    """Choose the cheapest valid response path and verify the final candidate."""

    version = "response-assembler-v8-natural-text"

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
                 fallback_locale="zh-CN", internal_tool_names=(), trace_sink=None) -> None:
        if fallback_locale not in {"zh-CN", "en"}:
            raise ValueError("unsupported customer fallback locale")
        self.fallback_locale = fallback_locale
        self._internal_tool_names = frozenset(internal_tool_names)
        self._composer = composer
        self._knowledge_verifier = knowledge_verifier
        self._knowledge_source_validator = knowledge_source_validator
        self._trace_sink = trace_sink

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
                ResponseAssemblyMode.TEMPLATE, (), False, "NOT_CHECKED", response.verification_reason,
                retryable=response.retryable, diagnostics=response.diagnostics)
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
        stage = "evidence_assembly"
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
                if requested_inputs:
                    return candidate
                if knowledge_facts or knowledge_failure:
                    if not candidate.diagnostics:
                        return self._knowledge_fallback(board, system_notice, unavailable=True)
                    return replace(self._knowledge_fallback(board, system_notice, unavailable=True),
                        verification_reason=candidate.verification_reason, retryable=candidate.retryable,
                        diagnostics=candidate.diagnostics)
                return candidate
            stage = "answer_verification"
            if self._knowledge_verifier is None:
                raise ValueError("answer support verifier unavailable")
            candidate, verdict = await self._verify_with_revision(
                board, current_message, candidate, conversation_context=conversation_context,
                system_notice=system_notice, pending_approval=pending_approval,
                requested_inputs=requested_inputs, knowledge_evidence=evidence)
            if not candidate.composer_used:
                if not requested_inputs and (knowledge_facts or knowledge_failure):
                    return replace(self._knowledge_fallback(board, system_notice, unavailable=True),
                        verification_reason=candidate.verification_reason, retryable=candidate.retryable,
                        diagnostics=candidate.diagnostics)
                return candidate
            if not verdict.publishable or not verdict.grounded:
                failed = self._failed(ValueError(verdict.reason), candidate.text, stage,
                                      code=verdict.reason_code.value)
                if knowledge_facts or knowledge_failure:
                    safe = self._knowledge_fallback(board, system_notice, unavailable=True)
                else:
                    safe = replace(failed, text=system_notice + _render_board(board, locale=self.fallback_locale))
                return replace(safe, verification_reason=failed.verification_reason,
                               verification_status=verdict.status.value.upper(),
                               diagnostics=(*candidate.diagnostics, *failed.diagnostics))
            stage = "citation_validation"
            cited = set(re.findall(r"\[(E[a-zA-Z0-9]+)\]", candidate.text))
            if cited - allowed:
                raise ValueError("answer citations do not match supplied evidence")
            stage = "source_validation"
            if packs and (self._knowledge_source_validator is None
                          or not self._knowledge_source_validator(packs)):
                raise ValueError("knowledge sources are no longer valid")
            return AssembledResponse(
                candidate.text, candidate.mode,
                candidate.evidence_refs,
                True, "PASS", "KNOWLEDGE_SUPPORT_CHECKED" if packs else "ANSWER_SUPPORT_CHECKED",
                hashlib.sha256(candidate.text.encode()).hexdigest(),
                evidence_sha256=candidate.evidence_sha256, evidence_json=candidate.evidence_json)
        except Exception as exc:
            failed = self._failed(exc, system_notice + _render_board(board, locale=self.fallback_locale), stage)
            if knowledge_facts or knowledge_failure:
                return replace(self._knowledge_fallback(board, system_notice, unavailable=True),
                    verification_reason=failed.verification_reason, retryable=failed.retryable,
                    diagnostics=failed.diagnostics)
            return failed

    async def _verify_with_revision(self, board, message, candidate, *,
                                    knowledge_evidence=None, conversation_context=None,
                                    system_notice="", pending_approval=None, requested_inputs=()):
        verdict = await self._verify_support(board, message, candidate.text,
            evidence_context=json.loads(candidate.evidence_json),
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
        if not revised.composer_used:
            return revised, verdict
        if revised.evidence_sha256 != candidate.evidence_sha256:
            raise ValueError("response revision changed original evidence")
        revised_verdict = await self._verify_support(board, message, revised.text,
            evidence_context=json.loads(revised.evidence_json),
            knowledge_evidence=knowledge_evidence, conversation_context=conversation_context, pending_approval=pending_approval,
            requested_inputs=requested_inputs)
        return revised, revised_verdict

    async def _verify_support(self, board, message, text, *, knowledge_evidence=None, conversation_context=None, pending_approval=None, requested_inputs=(), evidence_context=None):
        # The injected verifier is shared by business and knowledge composition.
        # Original facts, receipts and outcomes are evidence; generated summaries
        # are not promoted into independent proof of their own wording.
        inputs = dict(question=message, answer=text,
            context=json.dumps(evidence_context if evidence_context is not None else
                _response_context(board, pending_approval, requested_inputs, conversation_context), ensure_ascii=False),
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
                _evidence_refs(board), False,
                "NOT_CHECKED", "DETERMINISTIC_ASSEMBLY",
            )
        payload = {
            "schema_version": "conversation-compose-request-v5-text",
            "current_message": current_message,
            "conversation_context": conversation_context,
            "evidence": _response_context(board, pending_approval, requested_inputs, conversation_context),
            # Working text is context, never support for business claims.
            "domain_notes": [
                {"work_item_id": result.work_item_id, "text": _candidate_text(result)}
                for result in board.results if _candidate_text(result)
            ],
            "response_requirements": [
                *(["Cite policy claims with the supplied [E...] evidence IDs. Preserve conditions, exceptions and negation. Do not invent citation IDs."]
                  if any(f.requirement_id == "knowledge.active_source" for r in board.results for f in r.facts) else []),
                "Address the customer directly in the language they use or request. Do not include drafting notes, self-instructions or commentary about how to answer.",
                "Internal tool names, operation keys and raw parameter JSON are not customer explanations. Use supplied facts to explain item references; do not invent names, prices, fees or return instructions.",
                "COMMITTED receipts establish execution of their recorded actions. Use accompanying write-result facts for returned business state; earlier read observations or assistant messages do not establish non-execution after that action. Do not infer downstream settlement or delivery beyond the returned result.",
                *([_APPROVAL_DESCRIPTION_REQUIREMENT]
                  if not requested_inputs and any(c.kind == "PENDING_ACTION" for c in claims) else []),
            ],
        }
        if repair_feedback is not None:
            payload["repair_feedback"] = repair_feedback
        stage = "composition_model"
        try:
            # The captured evidence is immutable across authoring and revision.
            evidence_json = json.dumps(payload["evidence"], ensure_ascii=False,
                sort_keys=True, separators=(",", ":"))
            evidence_sha = hashlib.sha256(evidence_json.encode()).hexdigest()
            text = await self._composer.compose(payload)
            if not isinstance(text, str) or not text.strip():
                raise ValueError("composer must return nonempty text")
            text = text.strip()
        except Exception as exc:
            return self._failed(exc, system_notice + fallback, stage)
        return AssembledResponse(system_notice + text, mode, _evidence_refs(board), True,
            "PENDING", "COMPOSED_FROM_EVIDENCE", evidence_sha256=evidence_sha, evidence_json=evidence_json)

    def _failed(self, exc, text, stage, *, code=None):
        from core.framework_models import ModelInvocationError, retryable_model_error
        from core.tracing import exception_chain
        retryable = exc.retryable if isinstance(exc, ModelInvocationError) else retryable_model_error(exc)
        detail = {'code': code or type(exc).__name__, 'retryable': retryable,
                  'exception_chain': exception_chain(exc)}
        diagnostic = StageObservation(stage, StageStatus.FAILED, detail)
        logger.error("Response assembly failed: %s", json.dumps(diagnostic.to_dict(), ensure_ascii=False))
        if self._trace_sink:
            try:
                self._trace_sink.record_failure(diagnostic.to_dict())
            except Exception:
                logger.exception("Failure trace export failed; preserving the original diagnostic")
        return AssembledResponse(text, ResponseAssemblyMode.TEMPLATE, (), False, 'NOT_CHECKED',
                                code or stage + ':' + type(exc).__name__, retryable=retryable,
                                diagnostics=(diagnostic,))

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


def _failure_feedback(result):
    from application.work_recovery import failure_feedback
    return failure_feedback(result) if result.status not in _SUCCESS else []


def _input_context(requested_inputs):
    """Runtime-owned bindings, not model-selected evidence for factual claims."""
    return [{"target_work_item_id": spec.target_work_item_id, "field_name": spec.field_name,
             "value_schema": spec.value_schema, "question_hint": spec.question_hint}
            for spec in requested_inputs]


def _allowed_claims(board, pending_approval=None, *, requested_inputs=()) -> tuple[AllowedClaim, ...]:
    claims = []
    current = _current_board_facts(board)
    if pending_approval:
        claims.append(AllowedClaim("proposal:" + pending_approval.work_item_id, "PENDING_ACTION",
            {"action_ref": pending_approval.action_ref,
             "arguments": {arg.name: arg.value for arg in pending_approval.arguments},
             "effect_status": "NOT_EXECUTED"}, ()))
    for result in board.results:
        if result.pending_action and pending_approval is None:
            action = result.pending_action
            claims.append(AllowedClaim(
                f"proposal:{result.work_item_id}", "PENDING_ACTION",
                {"action_ref": action.action_ref, "objective": action.objective,
                 "arguments": {arg.name: arg.value for arg in action.arguments},
                 "effect_status": "NOT_EXECUTED"}, (),
            ))
        claims.append(AllowedClaim(f"outcome:{result.work_item_id}", "WORK_ITEM_OUTCOME",
            {"owner_agent": result.owner_agent, "status": result.status.value,
             "reason_code": result.reason_code}, result.evidence_refs))
        for index, fact in enumerate(result.facts, start=1):
            if fact not in current:
                continue
            claims.append(AllowedClaim(
                f"fact:{result.work_item_id}:{index}",
                "KNOWLEDGE_FACT" if fact.requirement_id == "knowledge.active_source" else
                "FACT",
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


def _response_context(board, pending_approval=None, requested_inputs=(), conversation_context=None):
    """One authoritative snapshot for authoring, verification and publication."""
    from dataclasses import asdict
    claims = _allowed_claims(board, pending_approval, requested_inputs=requested_inputs)
    return {
        "facts": [{"subject_ref": fact.subject_ref, "requirement_id": fact.requirement_id,
                   "source_kind": fact.source_kind.value, "source_ref": fact.source_ref,
                   "producer_id": fact.producer_id,
                   "producer_version": fact.producer_version, "observed_at": fact.observed_at.isoformat(),
                   "observation_started_at": fact.observation_started_at.isoformat() if fact.observation_started_at else None,
                   "valid_until": fact.valid_until.isoformat() if fact.valid_until else None,
                   "value": _fact_view(fact)} for fact in _current_board_facts(board)],
        "receipts": [asdict(receipt) for result in board.results for receipt in result.action_receipts],
        "pending_actions": [c.value for c in claims if c.kind == "PENDING_ACTION"],
        "requested_inputs": _input_context(requested_inputs),
        "outcomes": [{"work_item_id": r.work_item_id, "owner_agent": r.owner_agent,
                      "status": r.status.value, "reason_code": r.reason_code,
                      "retryable": r.retryable,
                      "execution_feedback": _failure_feedback(r)} for r in board.results],
        "coverage": {"missing_requirement_ids": list(board.missing_requirement_ids),
                     "conflict_keys": list(board.conflict_keys),
                     "coverage_complete": board.coverage_complete,
                     "complete": board.complete,
                     "partial_delivery_allowed": board.partial_delivery_allowed},
        "user_context": conversation_context,
    }


def _evidence_refs(board):
    """Runtime provenance, independent of the author's citation selection."""
    return tuple(dict.fromkeys([
        *(fact.source_ref for fact in _current_board_facts(board)),
        *(receipt.receipt_id for result in board.results for receipt in result.action_receipts),
    ]))


_APPROVAL_DESCRIPTION_REQUIREMENT = (
    "Approval description: explain the pending action's target, material changes and payment terms, "
    "state that it has not executed, and ask for approval."
)


def _message(locale, chinese, english):
    return english if locale == "en" else chinese


def _render_board(board, *, locale="zh-CN") -> str:
    from dataclasses import replace
    sections = []
    current = _current_board_facts(board)
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
        text = _render_verified_facts(replace(result, facts=tuple(f for f in result.facts if f in current)), locale=locale)
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
            rendered.append(_message(locale, "详细答复暂时未能完成核验。",
                "The detailed reply could not be verified."))
        sections.extend(rendered)
    return "\n".join(sections) or _message(locale, "暂时没有可发布的结果。", "No result is available yet.")


def _current_board_facts(board):
    return current_facts(tuple(fact for result in board.results for fact in result.facts))


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
        observed = fact.observed_at.isoformat()
        if fact.requirement_id == "refund.current_state":
            from services.customer_operation_views import refund_lookup_statements, UnsupportedRefundObservation
            try:
                observation = " ".join(text for _, text in refund_lookup_statements(value, locale=locale))
                texts.append(_message(locale, f"查询记录（{observed}）：{observation}",
                    f"Lookup observation ({observed}): {observation}"))
            except UnsupportedRefundObservation:
                texts.append(_message(locale, "当前退款查询结果格式无法确认，请重新查询。",
                    "The refund lookup returned an unrecognized result. Please query it again."))
        if fact.requirement_id == "order.current_state" and isinstance(value, dict):
            order_id, status = value.get("order_id"), value.get("status")
            if isinstance(order_id, str) and _REFERENCE.fullmatch(order_id) and isinstance(status, str) and status in statuses:
                texts.append(_message(locale, f"在 {observed} 的查询记录中，订单 {order_id} 状态为{statuses[status]}。",
                    f"The lookup at {observed} recorded order {order_id} as {status}."))
    return "\n".join(dict.fromkeys(texts))


def _fact_view(fact):
    value = json.loads(fact.value_json)
    if fact.requirement_id == "knowledge.active_source":
        from application.knowledge_tool_contract import model_evidence
        return model_evidence(value)
    return value
