"""用户输入 Prompt Injection 的确定性前置防线。

该 guard 只阻断高置信度的直接注入；它不是“彻底解决 Prompt Injection”的
声明。模型权限、工具审批、身份隔离和发布校验仍是攻击绕过检测后的主防线。
"""
from __future__ import annotations

import hashlib
import re
import threading
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Tuple


class InputSecurityAction(str, Enum):
    """用户输入 guard 的闭合处置集合。"""

    ALLOW = "allow"
    BLOCK = "block"


@dataclass(frozen=True)
class InputSecurityDecision:
    """不携带原始消息的安全判定。"""

    action: InputSecurityAction
    categories: Tuple[str, ...]
    risk_score: int
    input_fingerprint: str

    @property
    def blocked(self) -> bool:
        return self.action is InputSecurityAction.BLOCK


class PromptInjectionGuard:
    """检测系统规则覆盖、提示词窃取、角色伪造和授权伪造。"""

    _INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
    _EDUCATIONAL = re.compile(
        r"\b(?:what\s+is|explain|example|detect|defen[cs]e|research|tutorial|test\s+case)\b"
        r"|(?:什么是|解释|示例|检测|防御|研究|教程|测试用例|面试题)",
        re.IGNORECASE,
    )
    _ACTION_FOLLOWUP = re.compile(
        r"\b(?:then|now|execute|call|reveal|return|answer|perform|proceed)\b"
        r"|(?:然后|现在|执行|调用|返回|回答|继续操作|照做)",
        re.IGNORECASE,
    )
    _RULES = {
        "instruction_override": (
            re.compile(
                r"\b(?:ignore|disregard|forget|override|bypass)\b.{0,48}"
                r"\b(?:previous|prior|system|developer|safety|policy|rules?|instructions?)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:忽略|无视|忘掉|覆盖|绕过).{0,24}"
                r"(?:之前|先前|以上|系统|开发者|安全).{0,16}(?:指令|提示词|规则|策略|限制)",
                re.DOTALL,
            ),
        ),
        "prompt_exfiltration": (
            re.compile(
                r"\b(?:reveal|show|print|repeat|dump|expose|leak)\b.{0,48}"
                r"\b(?:system|developer|hidden|internal)\b.{0,24}"
                r"\b(?:prompt|message|instructions?|policy|secret)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:泄露|显示|输出|打印|复述|导出|告诉我).{0,28}"
                r"(?:系统提示词|系统指令|开发者指令|隐藏提示词|内部策略|密钥)",
                re.DOTALL,
            ),
        ),
        "role_spoofing": (
            re.compile(
                r"(?:^|\n)\s*(?:system|developer|assistant|tool)\s*[:：]"
                r"|<\s*/?\s*(?:system|developer|assistant|tool)\b"
                r"|#{2,}\s*(?:system|developer|assistant|tool)\b",
                re.IGNORECASE,
            ),
        ),
        "authorization_spoofing": (
            # "claim" is also a business noun ("your claim was approved").
            # Require a verbal complement before treating it as an instruction
            # to assert authority; passive application-status clauses are data.
            re.compile(
                r"\bclaim\s+(?:(?:falsely|incorrectly)\s+)?"
                r"(?:(?:that\s+)?(?:you|I|we|they|he|she|it|the\s+(?:user|customer|administrator|admin|operator|refund|request|action|tool|transaction))\b|to\s+(?:be|have)\b)"
                r".{0,48}\b(?:approved|authorized|administrator|admin|permission)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"\b(?:pretend|assume|mark|treat)\b.{0,40}"
                r"\b(?:approved|authorized|administrator|admin|permission)\b",
                re.IGNORECASE | re.DOTALL,
            ),
            re.compile(
                r"(?:假装|视为|声称|标记).{0,24}(?:已批准|已授权|管理员|拥有权限|审批通过)",
                re.DOTALL,
            ),
        ),
        "unrestricted_persona": (
            re.compile(
                r"\b(?:developer\s+mode|do\s+anything\s+now|unrestricted\s+(?:ai|assistant)|no\s+rules)\b",
                re.IGNORECASE,
            ),
            re.compile(r"(?:开发者模式|无限制(?:助手|模式)|没有任何规则|解除所有限制)"),
        ),
        "encoding_evasion": (
            re.compile(
                r"\b(?:decode|encode|base64|rot13|unicode\s+escape)\b.{0,48}"
                r"\b(?:follow|execute|instruction|bypass|filter)\b",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    }
    _WEIGHTS = {
        "instruction_override": 3,
        "prompt_exfiltration": 4,
        "role_spoofing": 2,
        "authorization_spoofing": 4,
        "unrestricted_persona": 3,
        "encoding_evasion": 2,
    }

    def __init__(self):
        self._lock = threading.RLock()
        self._total = 0
        self._flagged = 0
        self._blocked = 0

    def analyze(self, message: str) -> InputSecurityDecision:
        """规范化不可见字符后检测；只对高置信组合 fail closed。"""
        normalized = self._normalize(message)
        categories = tuple(
            category
            for category, patterns in self._RULES.items()
            if any(pattern.search(normalized) for pattern in patterns)
        )
        score = sum(self._WEIGHTS[category] for category in categories)
        educational = bool(self._EDUCATIONAL.search(normalized))
        requests_action = bool(self._ACTION_FOLLOWUP.search(normalized))
        category_set = set(categories)
        blocked = (
            "prompt_exfiltration" in category_set
            or "authorization_spoofing" in category_set
            or (
                bool(category_set & {"instruction_override", "unrestricted_persona"})
                and (not educational or requests_action)
            )
            or (
                "role_spoofing" in category_set
                and "instruction_override" in category_set
            )
        )
        decision = InputSecurityDecision(
            action=InputSecurityAction.BLOCK if blocked else InputSecurityAction.ALLOW,
            categories=categories,
            risk_score=score,
            input_fingerprint=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        )
        with self._lock:
            self._total += 1
            self._flagged += int(bool(categories))
            self._blocked += int(blocked)
        return decision

    def get_stats(self) -> Dict[str, int]:
        """返回不含消息正文的进程内观测计数。"""
        with self._lock:
            return {
                "total": self._total,
                "flagged": self._flagged,
                "blocked": self._blocked,
            }

    @classmethod
    def _normalize(cls, message: str) -> str:
        text = unicodedata.normalize("NFKC", str(message or ""))
        text = cls._INVISIBLE.sub("", text)
        return re.sub(r"[\t\r\f\v ]+", " ", text).strip()


class UntrustedContentGuard:
    """Quarantine indirect instructions from Memory/RAG/tool-produced data."""

    def __init__(self):
        self._classifier = PromptInjectionGuard()

    def analyze(self, content: str) -> InputSecurityDecision:
        classified = self._classifier.analyze(content)
        if not classified.categories:
            return classified
        return InputSecurityDecision(
            action=InputSecurityAction.BLOCK,
            categories=classified.categories,
            risk_score=classified.risk_score,
            input_fingerprint=classified.input_fingerprint,
        )
