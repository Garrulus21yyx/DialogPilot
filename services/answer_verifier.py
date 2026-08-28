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

from core.llm_utils import extract_text_content


class VerificationStatus(str, Enum):
    """校验器支持的闭合结果集合；新增状态必须同步迁移所有消费者。"""

    PASS = "pass"
    REJECT = "reject"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerificationResult:
    """一次校验的不可变结果，同时携带依据性与人工升级信号。"""

    status: VerificationStatus
    grounded: bool
    need_escalation: bool
    reason: str

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
        self._model = model

    async def verify(self, question: str, answer: str, context: str = "") -> VerificationResult:
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

只返回：
{{"status":"pass|reject|unknown","grounded":true,"reason":"简短原因"}}
""".strip()

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=256,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = extract_text_content(response.content)
            payload = self._parse_payload(raw)
            status = VerificationStatus(str(payload["status"]).lower())
            grounded = bool(payload.get("grounded", False))
            reason = str(payload.get("reason", "verification completed")).strip()[:300]
            return VerificationResult(
                status=status,
                grounded=grounded,
                need_escalation=status is not VerificationStatus.PASS,
                reason=reason,
            )
        except Exception as exc:
            # 校验服务故障不能证明候选回答安全，因此统一进入人工路径。
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                grounded=False,
                need_escalation=True,
                reason=f"verification unavailable: {type(exc).__name__}",
            )

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
