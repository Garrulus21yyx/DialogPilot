"""Typed answer verification boundary for user-visible Agent responses."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

from anthropic import AsyncAnthropic

from core.llm_utils import extract_text_content


class VerificationStatus(str, Enum):
    """Closed set of outcomes produced by the verification boundary."""

    PASS = "pass"
    REJECT = "reject"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    grounded: bool
    need_escalation: bool
    reason: str

    @property
    def publishable(self) -> bool:
        return self.status is VerificationStatus.PASS


class AnswerVerifier:
    """Verify an answer before it crosses the user-visible API boundary.

    Model failures and malformed output become ``UNKNOWN``. They never become
    an implicit pass, so unsupported verifier states fail closed and are
    routed to human review.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        client: Optional[Any] = None,
    ):
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
        question = (question or "").strip()
        answer = (answer or "").strip()
        context = (context or "").strip()

        if not answer:
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
            return VerificationResult(
                status=VerificationStatus.UNKNOWN,
                grounded=False,
                need_escalation=True,
                reason=f"verification unavailable: {type(exc).__name__}",
            )

    @staticmethod
    def _parse_payload(raw: str) -> Dict[str, Any]:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            raise ValueError("verifier response does not contain a JSON object")
        payload = json.loads(raw[start : end + 1])
        if not isinstance(payload, dict) or "status" not in payload:
            raise ValueError("verifier response has no status")
        return payload
