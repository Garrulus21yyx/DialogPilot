"""回答发布前的有类型校验边界。

该模块只判断候选回答是否具备发布资格，不拥有回答内容。任何模型异常、
格式错误或未知状态都会收敛为 ``UNKNOWN``，确保发布边界默认拒绝而非放行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from anthropic import AsyncAnthropic

from core.llm_metrics import create_message
from core.llm_utils import extract_text_content
from core.model_policy import ModelProfile, ModelRole


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


@dataclass(frozen=True)
class VerificationResult:
    """一次校验的不可变结果，同时携带依据性与人工升级信号。"""

    status: VerificationStatus
    grounded: bool
    need_escalation: bool
    reason: str
    reason_code: VerificationReasonCode

    @property
    def publishable(self) -> bool:
        """只有明确的 PASS 才能越过用户可见的发布边界。"""
        return self.status is VerificationStatus.PASS


class AnswerVerifier:
    """在回答进入用户可见 API 前进行校验。

    模型不可用或输出损坏时返回 ``UNKNOWN``，绝不隐式视作通过；因此所有
    不受支持的状态都会 fail-closed，并交由人工复核。
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        client: Optional[Any] = None,
        model_profile: Optional[ModelProfile] = None,
    ):
        """注入兼容 Anthropic Messages API 的客户端；未注入时按配置创建。"""
        if client is None:
            if not api_key:
                raise ValueError("api_key is required when client is not supplied")
            kwargs: Dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = AsyncAnthropic(**kwargs)
        self._client = client
        self._model_profile = model_profile or ModelProfile(model)
        self._model = self._model_profile.model

    async def verify(
        self,
        question: str,
        answer: str,
        context: str = "",
        *,
        task_plan: Optional[Dict[str, Any]] = None,
        coverage: Optional[Dict[str, Any]] = None,
        agent_outcomes: Optional[list[Dict[str, Any]]] = None,
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
        unresolved = coverage.get("unresolved_required_task_ids")
        if coverage.get("complete") is False or (isinstance(unresolved, list) and unresolved):
            unresolved_text = ", ".join(str(item) for item in (unresolved or [])) or "unknown"
            return VerificationResult(
                status=VerificationStatus.REJECT,
                grounded=False,
                need_escalation=True,
                reason=f"required tasks are unresolved: {unresolved_text}"[:300],
                reason_code=VerificationReasonCode.INCOMPLETE,
            )

        orchestration_evidence = json.dumps(
            {
                "task_plan": task_plan or {},
                "coverage": coverage,
                "agent_outcomes": agent_outcomes or [],
            },
            ensure_ascii=False,
        )

        prompt = f"""
你是客服回答发布前的质量校验器。根据用户问题、候选回答和可选上下文，返回严格 JSON。

状态定义：
- pass：回答解决了问题，且没有编造高风险事实。
- reject：回答明显错误、危险、与问题无关，或声称执行了实际并未执行的操作。
- unknown：证据不足，无法可靠判断。

用户问题：{question}
候选回答：{answer}
上下文：{context or "（无外部知识上下文）"}
任务执行证据：{orchestration_evidence}

只返回：
{{"status":"pass|reject|unknown","grounded":true,"reason_code":"passed|incomplete|ungrounded|unsafe|irrelevant|model_rejected","reason":"简短原因"}}
""".strip()

        try:
            response = await create_message(
                self._client,
                self._model_profile,
                ModelRole.VERIFIER,
                max_tokens=256,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(response.content)
            payload = self._parse_payload(raw)
            status = VerificationStatus(str(payload["status"]).lower())
            grounded = bool(payload.get("grounded", False))
            reason = str(payload.get("reason", "verification completed")).strip()[:300]
            reason_code = self._reason_code(payload.get("reason_code"), status, grounded)
            return VerificationResult(
                status=status,
                grounded=grounded,
                need_escalation=status is not VerificationStatus.PASS,
                reason=reason,
                reason_code=reason_code,
            )
        except Exception as exc:
            # 校验服务故障不能证明候选回答安全，因此统一进入人工路径。
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                grounded=False,
                need_escalation=True,
                reason=f"verification unavailable: {type(exc).__name__}",
                reason_code=VerificationReasonCode.VERIFIER_UNAVAILABLE,
            )

    @staticmethod
    def _reason_code(
        value: Any,
        status: VerificationStatus,
        grounded: bool,
    ) -> VerificationReasonCode:
        """规范模型原因码，并为缺失/未知值提供保守默认。"""
        if status is VerificationStatus.PASS:
            return VerificationReasonCode.PASSED
        if status is VerificationStatus.UNKNOWN:
            return VerificationReasonCode.VERIFIER_UNAVAILABLE
        try:
            code = VerificationReasonCode(str(value).lower())
        except ValueError:
            code = (
                VerificationReasonCode.UNGROUNDED
                if not grounded
                else VerificationReasonCode.MODEL_REJECTED
            )
        if code is VerificationReasonCode.PASSED:
            return VerificationReasonCode.MODEL_REJECTED
        return code

    @staticmethod
    def _parse_payload(raw: str) -> Dict[str, Any]:
        """从可能带前后说明文字的响应中提取唯一 JSON 对象。"""
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("verifier response does not contain a JSON object")
        payload = json.loads(raw[start : end + 1])
        if not isinstance(payload, dict) or "status" not in payload:
            raise ValueError("verifier response has no status")
        return payload
