"""有类型的上下文装配与轻量 Token 预算工具。

这里拥有“哪些数据进入模型输入、按什么优先级裁剪”的转换合同；记忆和知识
模块只生产上下文事实，不应各自拼装最终 Prompt。
"""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple


class TokenEstimator:
    """与供应商无关的快速估算器，仅用于调用前预算而非精确计费。"""

    @staticmethod
    def estimate(value: Any) -> int:
        """按中文字符与其他字符的经验比例估算 Token 数。"""
        if value is None:
            return 0
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True
        )
        units = 0.0
        for char in text:
            code = ord(char)
            if 0x3400 <= code <= 0x9FFF:
                units += 2 / 3
            else:
                units += 1 / 4
        return max(1, math.ceil(units)) if text else 0

    @classmethod
    def estimate_messages(cls, messages: Iterable[Dict[str, Any]]) -> int:
        """估算消息正文、工具字段及每条消息的协议开销。"""
        total = 0
        for message in messages:
            total += 4
            total += cls.estimate(message.get("content", ""))
            total += cls.estimate(message.get("tool_calls"))
            total += cls.estimate(message.get("tool_call_id"))
        return total

    @classmethod
    def truncate(cls, text: str, max_tokens: int) -> str:
        """用二分查找截取不超过预算的最长文本前缀。"""
        text = str(text or "")
        if max_tokens <= 0:
            return ""
        if cls.estimate(text) <= max_tokens:
            return text
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if cls.estimate(text[:mid]) <= max_tokens:
                low = mid
            else:
                high = mid - 1
        clipped = text[:low].rstrip()
        return f"{clipped}…" if clipped else ""


@dataclass(frozen=True)
class ContextSection:
    """带来源标签、优先级和信任边界的内部上下文片段。"""

    tag: str
    content: str
    description: str = ""
    priority: int = 50
    untrusted_data: bool = True

    def render(self, content: str | None = None) -> str:
        """转义不可信数据并渲染为带 ``data_only`` 标记的标签块。"""
        safe_tag = re.sub(r"[^a-zA-Z0-9_-]", "_", self.tag) or "context"
        safe_content = html.escape(self.content if content is None else content, quote=False)
        attributes = []
        if self.description:
            attributes.append(f'description="{html.escape(self.description, quote=True)}"')
        if self.untrusted_data:
            attributes.append('data_only="true"')
        attrs = f" {' '.join(attributes)}" if attributes else ""
        return f"<{safe_tag}{attrs}>\n{safe_content}\n</{safe_tag}>"


@dataclass(frozen=True)
class PromptContext:
    """已完成预算的内部上下文，只在一个边界转换为供应商消息。"""

    system_context: str
    history: Tuple[Dict[str, str], ...]
    estimated_tokens: int
    dropped_history: int = 0
    truncated_sections: Tuple[str, ...] = field(default_factory=tuple)

    def to_messages(self, current_user_message: str) -> List[Dict[str, str]]:
        """复制历史并将当前用户消息追加为最后一条真实对话。"""
        messages = [dict(message) for message in self.history]
        messages.append({"role": "user", "content": str(current_user_message or "")})
        return messages


class ContextAssembler:
    """上下文状态到有界 LLM Prompt 转换的唯一 Owner。"""

    def __init__(
        self,
        max_input_tokens: int = 12000,
        reserved_output_tokens: int = 1536,
        fixed_system_reserve: int = 768,
        section_ratio: float = 0.55,
    ):
        """配置输入、输出和固定系统开销，并校验预算存在可用空间。"""
        if max_input_tokens <= reserved_output_tokens + fixed_system_reserve:
            raise ValueError("max_input_tokens must exceed reserved prompt budgets")
        self.max_input_tokens = int(max_input_tokens)
        self.reserved_output_tokens = int(reserved_output_tokens)
        self.fixed_system_reserve = int(fixed_system_reserve)
        self.section_ratio = min(max(float(section_ratio), 0.2), 0.8)
        self.estimator = TokenEstimator()

    def assemble(
        self,
        *,
        sections: Sequence[ContextSection],
        history: Sequence[Dict[str, Any]],
        current_user_message: str,
    ) -> PromptContext:
        """按优先级装配 section，并从最近历史向前保留消息。"""
        current_tokens = self.estimator.estimate_messages(
            [{"role": "user", "content": current_user_message}]
        )
        available = max(
            1,
            self.max_input_tokens
            - self.reserved_output_tokens
            - self.fixed_system_reserve
            - current_tokens,
        )

        section_budget = max(1, int(available * self.section_ratio))
        rendered_sections, used_section_tokens, truncated = self._fit_sections(
            sections, section_budget
        )
        history_budget = available - used_section_tokens
        fitted_history, used_history_tokens, dropped = self._fit_history(
            history, history_budget
        )

        # 历史实际占用较少时，把闲置容量返还给 section，避免不必要的知识裁剪。
        unused = max(0, history_budget - used_history_tokens)
        if truncated and unused:
            rendered_sections, used_section_tokens, truncated = self._fit_sections(
                sections, section_budget + unused
            )

        system_context = "\n\n".join(rendered_sections)
        estimated = (
            self.fixed_system_reserve
            + self.estimator.estimate(system_context)
            + self.estimator.estimate_messages(fitted_history)
            + current_tokens
            + self.reserved_output_tokens
        )
        return PromptContext(
            system_context=system_context,
            history=tuple(fitted_history),
            estimated_tokens=estimated,
            dropped_history=dropped,
            truncated_sections=tuple(truncated),
        )

    def _fit_sections(
        self, sections: Sequence[ContextSection], budget: int
    ) -> Tuple[List[str], int, List[str]]:
        """优先装入高优先级 section，最终恢复调用方原始展示顺序。"""
        selected: Dict[int, str] = {}
        truncated: List[str] = []
        remaining = max(0, budget)
        ordered = sorted(enumerate(sections), key=lambda item: (-item[1].priority, item[0]))
        for index, section in ordered:
            if not str(section.content or "").strip() or remaining <= 0:
                continue
            wrapper_tokens = self.estimator.estimate(section.render(""))
            content_budget = remaining - wrapper_tokens
            if content_budget <= 0:
                truncated.append(section.tag)
                continue
            fitted = self.estimator.truncate(section.content, content_budget)
            rendered = section.render(fitted)
            cost = self.estimator.estimate(rendered)
            if cost > remaining:
                truncated.append(section.tag)
                continue
            selected[index] = rendered
            remaining -= cost
            if fitted != section.content:
                truncated.append(section.tag)
        rendered = [selected[index] for index in sorted(selected)]
        return rendered, budget - remaining, truncated

    def _fit_history(
        self, history: Sequence[Dict[str, Any]], budget: int
    ) -> Tuple[List[Dict[str, str]], int, int]:
        """过滤非法历史角色，并从最新消息向前装入直至预算耗尽。"""
        valid = [
            {"role": str(item.get("role")), "content": str(item.get("content") or "")}
            for item in history
            if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip()
        ]
        kept_reversed: List[Dict[str, str]] = []
        used = 0
        for message in reversed(valid):
            cost = self.estimator.estimate_messages([message])
            if used + cost > max(0, budget):
                break
            kept_reversed.append(message)
            used += cost
        kept = list(reversed(kept_reversed))
        return kept, used, len(valid) - len(kept)
