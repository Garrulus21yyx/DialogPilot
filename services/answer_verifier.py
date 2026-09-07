"""回答发布前的有类型校验边界。

该模块只判断候选回答是否具备发布资格，不拥有回答内容。任何模型异常、
格式错误或未知状态都会收敛为 ``UNKNOWN``，确保发布边界默认拒绝而非放行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Dict, Optional

from core.model_policy import ModelProfile
from services.claim_verification import AnswerAssessment, verify_claims, fingerprint


class VerificationStatus(str, Enum):
    """校验器支持的闭合结果集合；新增状态必须同步迁移所有消费者。"""

    PASS = "pass"
    REJECT = "reject"
    UNKNOWN = "unknown"


class VerificationReasonCode(str, Enum):
    """发布拒绝的稳定机器可读原因；展示文本不再承担控制流。"""

    PASSED = "passed"
    EMPTY_ANSWER = "empty_answer"
    INCOMPLETE = "incomplete"
    UNGROUNDED = "ungrounded"
    UNSAFE = "unsafe"
    IRRELEVANT = "irrelevant"
    MODEL_REJECTED = "model_rejected"
    VERIFIER_UNAVAILABLE = "verifier_unavailable"
    INVALID_CONTRACT = "invalid_contract"
    APPROVAL_REQUIRED = "approval_required"
    POLICY_TERMINAL = "policy_terminal"


@dataclass(frozen=True)
class VerificationResult:
    """一次校验的不可变结果，同时携带依据性与人工升级信号。"""

    status: VerificationStatus
    grounded: bool
    need_escalation: bool
    reason: str
    reason_code: VerificationReasonCode
    assessment: AnswerAssessment | None = None
    request_binding: str = ""

    def bind_to(self, question, answer, context="", **evidence):
        return replace(self, request_binding=_binding(question, answer, context, **evidence))

    def matches_request(self, question, answer, context="", **evidence):
        return bool(self.request_binding) and self.request_binding == _binding(question, answer, context, **evidence)

    @property
    def publishable(self) -> bool:
        """只有明确的 PASS 才能越过用户可见的发布边界。"""
        return (bool(self.request_binding) and self.status is VerificationStatus.PASS
                and self.grounded is True and self.reason_code is VerificationReasonCode.PASSED
                and self.assessment is not None
                and self.assessment.supported and self.assessment.answered)



def _binding(question, answer, context="", *, task_plan=None, coverage=None,
             agent_outcomes=None, knowledge_evidence=None):
    return fingerprint({"question": question, "answer": answer, "context": context,
        "task_plan": task_plan or {}, "coverage": coverage or {},
        "agent_outcomes": agent_outcomes or [], "knowledge_evidence": knowledge_evidence or {}})


class AnswerVerifier:
    """在回答进入用户可见 API 前进行校验。

    模型不可用或输出损坏时返回 ``UNKNOWN``，绝不隐式视作通过；因此所有
    不受支持的状态都会 fail-closed，并交由人工复核。
    """

    def __init__(
        self,
        model_client: Any,
        *,
        model_profile: ModelProfile,
        callbacks=(),
    ):
        """Inject the same framework model contract used by Target planning."""
        self._client = model_client
        self._callbacks = callbacks
        self._model_profile = model_profile
        self._model = self._model_profile.model

    async def verify(self, question, answer, context="", *, task_plan=None, coverage=None,
                     agent_outcomes=None, knowledge_evidence=None):
        try:
            if not all(isinstance(value, str) for value in (question, answer, context)):
                raise ValueError('question, answer and context must be text')
            inputs = json.loads(json.dumps({"question": question, "answer": answer, "context": context,
                "task_plan": task_plan, "coverage": coverage, "agent_outcomes": agent_outcomes,
                "knowledge_evidence": knowledge_evidence}, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError):
            return VerificationResult(
                status=VerificationStatus.UNKNOWN, grounded=False, need_escalation=True,
                reason='verification input is not a supported JSON snapshot',
                reason_code=VerificationReasonCode.INVALID_CONTRACT,
            )
        result = await self._verify(**inputs)
        return result.bind_to(**inputs)

    async def _verify(
        self,
        question: str,
        answer: str,
        context: str = "",
        *,
        task_plan: Optional[Dict[str, Any]] = None,
        coverage: Optional[Dict[str, Any]] = None,
        agent_outcomes: Optional[list[Dict[str, Any]]] = None,
        knowledge_evidence: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """校验候选回答，并把任意外部异常转换成闭合的有类型结果。"""
        question = (question or "").strip()
        answer = (answer or "").strip()
        context = (context or "").strip()

        if not answer:
            # 空回答是确定性的业务失败，无需浪费一次模型调用。
            return VerificationResult(
                status=VerificationStatus.REJECT,
                grounded=False,
                need_escalation=True,
                reason="Agent returned an empty answer",
                reason_code=VerificationReasonCode.EMPTY_ANSWER,
            )

        coverage = coverage or {}
        knowledge_evidence = knowledge_evidence or {}
        if knowledge_evidence.get("mode") is not None:
            return VerificationResult(VerificationStatus.UNKNOWN, False, True,
                "unsupported preverified evidence mode", VerificationReasonCode.INVALID_CONTRACT)

        try:
            try:
                context_data = json.loads(context)
            except (ValueError, TypeError):
                context_data = context
            approval_required = isinstance(context_data, dict) and bool(context_data.get("pending_actions"))
            evidence = {
                "approval_required": approval_required,
                "context": context_data,
                "task_plan": task_plan or {},
                "coverage": coverage,
                "agent_outcomes": agent_outcomes or [],
                "knowledge_evidence": knowledge_evidence,
            }
            assessment = await verify_claims(
                self._client, self._model_profile, question=question,
                answer=answer, evidence=evidence, callbacks=self._callbacks,
            )
            supported = assessment.supported
            missing_outcomes = (context_data.get("unrepresented_outcomes", [])
                                if isinstance(context_data, dict) else [])
            complete = assessment.answered and not missing_outcomes
            approval_complete = not approval_required or assessment.approval_terms_complete
            status = VerificationStatus.PASS if supported and complete and approval_complete else VerificationStatus.REJECT
            reason_code = (VerificationReasonCode.UNGROUNDED if not supported else
                           VerificationReasonCode.INCOMPLETE if not complete else
                           VerificationReasonCode.APPROVAL_REQUIRED if not approval_complete else
                           VerificationReasonCode.PASSED)
            return VerificationResult(
                status=status, grounded=supported,
                need_escalation=status is not VerificationStatus.PASS,
                reason=("Explain unresolved task outcomes: " + ", ".join(missing_outcomes)
                        if missing_outcomes else "; ".join(assessment.issues)) or (
                    "approval description or confirmation question is incomplete" if not approval_complete else
                    "answer supported and request addressed"),
                reason_code=reason_code, assessment=assessment,
            )
        except Exception as exc:
            return VerificationResult(
                status=VerificationStatus.UNKNOWN, grounded=False, need_escalation=True,
                reason=f"verification unavailable: {type(exc).__name__}",
                reason_code=(VerificationReasonCode.INVALID_CONTRACT
                             if isinstance(exc, (ValueError, TypeError))
                             else VerificationReasonCode.VERIFIER_UNAVAILABLE),
            )
