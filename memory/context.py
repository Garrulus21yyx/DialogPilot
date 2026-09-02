"""有类型的上下文装配与轻量 Token 预算工具。

这里拥有“哪些数据进入模型输入、按什么优先级裁剪”的转换合同；记忆和知识
模块只生产上下文事实，不应各自拼装最终 Prompt。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from core.input_security import UntrustedContentGuard
from core.token_estimator import TokenEstimator


class ContextBudgetExceededError(ValueError):
    """强制保留内容已超出输入预算，调用方必须缩短当前轮次或提高上限。"""

    def __init__(self, *, required_tokens: int, max_input_tokens: int):
        self.required_tokens = int(required_tokens)
        self.max_input_tokens = int(max_input_tokens)
        super().__init__(
            f"mandatory context requires {self.required_tokens} tokens, "
            f"exceeding max_input_tokens={self.max_input_tokens}"
        )


class ContextPolicyError(ValueError):
    pass


class ContextNode(str, Enum):
    LEGACY = "legacy"
    ROUTER = "router"
    PLANNER = "planner"
    WORKER = "worker"
    VERIFIER = "verifier"
    SYNTHESIS = "synthesis"


class ContextSelectionStatus(str, Enum):
    SELECTED = "SELECTED"
    TRUNCATED = "TRUNCATED"
    DROPPED_POLICY = "DROPPED_POLICY"
    DROPPED_BUDGET = "DROPPED_BUDGET"
    QUARANTINED = "QUARANTINED"
    EMPTY = "EMPTY"


@dataclass(frozen=True)
class ContextSelectionDecision:
    tag: str
    status: ContextSelectionStatus
    reason: str
    policy_priority: int
    legacy_priority: int


class ContextPolicyV1:
    """Versioned node/task/route selection and section priority owner."""

    version = "context-policy-v1"
    priorities: Mapping[str, int] = {
        "active_tickets": 90,
        "knowledge": 85,
        "user_profile": 75,
        "relevant_history": 65,
        "conversation_summary": 55,
    }
    node_allowlists: Mapping[ContextNode, frozenset[str] | None] = {
        ContextNode.LEGACY: None,
        ContextNode.ROUTER: frozenset({
            "active_tickets", "service_continuity", "conversation_summary",
            "user_profile", "open_commitments",
        }),
        ContextNode.PLANNER: None,
        ContextNode.WORKER: None,
        ContextNode.VERIFIER: frozenset({
            "knowledge", "active_tickets", "tool_receipts",
            "conversation_summary", "service_continuity",
        }),
        ContextNode.SYNTHESIS: frozenset({
            "agent_outcomes", "tool_receipts", "knowledge", "active_tickets",
        }),
    }
    supported_routes = frozenset({
        "legacy", "direct", "knowledge_qa", "agent_task", "mixed",
        "multi_domain", "handoff", "clarify", "out_of_scope",
    })

    def select(
        self,
        sections: Sequence["ContextSection"],
        *,
        node: ContextNode,
        route: str,
        task_context_refs: Sequence[str],
    ) -> tuple[tuple["ContextSection", ...], tuple[ContextSelectionDecision, ...]]:
        if route not in self.supported_routes:
            raise ContextPolicyError(f"unsupported context route: {route}")
        allowlist = self.node_allowlists[node]
        task_tags = {
            str(ref) for ref in task_context_refs
            if str(ref).strip() and not str(ref).startswith("entity:")
        }
        selected, decisions = [], []
        for section in sections:
            priority = int(self.priorities.get(section.tag, section.priority))
            if not str(section.content or "").strip():
                decisions.append(ContextSelectionDecision(
                    section.tag, ContextSelectionStatus.EMPTY,
                    "empty section has no provider input value",
                    priority, section.priority,
                ))
                continue
            allowed = allowlist is None or section.tag in allowlist
            if node is ContextNode.WORKER and task_tags:
                allowed = allowed and section.tag in task_tags
            if not allowed:
                decisions.append(ContextSelectionDecision(
                    section.tag, ContextSelectionStatus.DROPPED_POLICY,
                    f"not selected for node={node.value}/route={route}",
                    priority, section.priority,
                ))
                continue
            selected.append(replace(section, priority=priority))
            decisions.append(ContextSelectionDecision(
                section.tag, ContextSelectionStatus.SELECTED,
                f"selected by {self.version} for node={node.value}/route={route}",
                priority, section.priority,
            ))
        return tuple(selected), tuple(decisions)


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
    section_blocks: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    quarantined_sections: Tuple[str, ...] = field(default_factory=tuple)
    policy_version: str = "context-policy-v1"
    node: str = ContextNode.LEGACY.value
    route: str = "legacy"
    selection_trace: Tuple[ContextSelectionDecision, ...] = field(default_factory=tuple)

    def to_messages(self, current_user_message: str) -> List[Dict[str, str]]:
        """复制历史并将当前用户消息追加为最后一条真实对话。"""
        messages = [dict(message) for message in self.history]
        messages.append({"role": "user", "content": str(current_user_message or "")})
        return messages

    def scoped(
        self,
        *,
        section_tags: Sequence[str],
        include_history: bool,
    ) -> "PromptContext":
        """按 TaskGraph 声明的来源生成只读子上下文。

        ``section_blocks`` 是 ContextAssembler 产生的权威投影。旧对象若没有
        结构化 block，返回空 section 而不回退到全量字符串，避免隔离失败。
        """
        allowed = {str(tag) for tag in section_tags if str(tag).strip()}
        blocks = tuple(
            (tag, rendered)
            for tag, rendered in self.section_blocks
            if tag in allowed
        )
        return PromptContext(
            system_context="\n\n".join(rendered for _, rendered in blocks),
            history=self.history if include_history else (),
            # 原始值是严格上界；子集不可能比原输入更大。
            estimated_tokens=self.estimated_tokens,
            dropped_history=self.dropped_history + (0 if include_history else len(self.history)),
            truncated_sections=tuple(
                tag for tag in self.truncated_sections if tag in allowed
            ),
            section_blocks=blocks,
            quarantined_sections=self.quarantined_sections,
            policy_version=self.policy_version,
            node=self.node,
            route=self.route,
            selection_trace=tuple(
                decision for decision in self.selection_trace
                if decision.tag in allowed
            ),
        )


class ContextAssembler:
    """上下文状态到有界 LLM Prompt 转换的唯一 Owner。"""

    def __init__(
        self,
        max_input_tokens: int = 12000,
        reserved_output_tokens: int = 1536,
        fixed_system_reserve: int = 768,
        section_ratio: float = 0.55,
        policy: ContextPolicyV1 | None = None,
    ):
        """配置输入、输出和固定系统开销，并校验预算存在可用空间。"""
        if max_input_tokens <= reserved_output_tokens + fixed_system_reserve:
            raise ValueError("max_input_tokens must exceed reserved prompt budgets")
        self.max_input_tokens = int(max_input_tokens)
        self.reserved_output_tokens = int(reserved_output_tokens)
        self.fixed_system_reserve = int(fixed_system_reserve)
        self.section_ratio = min(max(float(section_ratio), 0.2), 0.8)
        self.estimator = TokenEstimator()
        self.untrusted_content_guard = UntrustedContentGuard()
        self.policy = policy or ContextPolicyV1()

    def assemble(
        self,
        *,
        sections: Sequence[ContextSection],
        history: Sequence[Dict[str, Any]],
        current_user_message: str,
        node: ContextNode | str = ContextNode.LEGACY,
        route: str = "legacy",
        task_context_refs: Sequence[str] = (),
    ) -> PromptContext:
        """按优先级装配 section，并保证每个成功结果都满足总预算不变量。"""
        try:
            resolved_node = ContextNode(node)
        except ValueError as exc:
            raise ContextPolicyError(f"unsupported context node: {node}") from exc
        policy_sections, policy_decisions = self.policy.select(
            sections, node=resolved_node, route=route,
            task_context_refs=task_context_refs,
        )
        safe_sections: List[ContextSection] = []
        quarantined: List[str] = []
        for section in policy_sections:
            if (
                section.untrusted_data
                and self.untrusted_content_guard.analyze(section.content).blocked
            ):
                quarantined.append(section.tag)
            else:
                safe_sections.append(section)

        current_tokens = self.estimator.estimate_messages(
            [{"role": "user", "content": current_user_message}]
        )
        mandatory_tokens = (
            self.reserved_output_tokens + self.fixed_system_reserve + current_tokens
        )
        if mandatory_tokens > self.max_input_tokens:
            raise ContextBudgetExceededError(
                required_tokens=mandatory_tokens,
                max_input_tokens=self.max_input_tokens,
            )
        available = self.max_input_tokens - mandatory_tokens

        section_budget = int(available * self.section_ratio)
        rendered_sections, used_section_tokens, truncated, section_blocks = self._fit_sections(
            safe_sections, section_budget
        )
        history_budget = available - used_section_tokens
        fitted_history, used_history_tokens, dropped = self._fit_history(
            history, history_budget
        )

        # 历史确定后，section 的最终上限就是剩余总容量；不能把首轮未使用容量再加一次。
        final_section_budget = max(0, available - used_history_tokens)
        if truncated and final_section_budget > used_section_tokens:
            rendered_sections, used_section_tokens, truncated, section_blocks = self._fit_sections(
                safe_sections, final_section_budget
            )

        system_context = "\n\n".join(rendered_sections)
        estimated = (
            self.fixed_system_reserve
            + self.estimator.estimate(system_context)
            + self.estimator.estimate_messages(fitted_history)
            + current_tokens
            + self.reserved_output_tokens
        )
        if estimated > self.max_input_tokens:
            # 这里表示装配 Owner 自己破坏了代数，不允许把超限 Prompt 交给下游。
            raise ContextBudgetExceededError(
                required_tokens=estimated,
                max_input_tokens=self.max_input_tokens,
            )
        final_decisions = []
        block_tags = {tag for tag, _ in section_blocks}
        truncated_tags = set(truncated)
        quarantined_tags = set(quarantined)
        for decision in policy_decisions:
            status, reason = decision.status, decision.reason
            if status is ContextSelectionStatus.SELECTED:
                if decision.tag in quarantined_tags:
                    status, reason = (
                        ContextSelectionStatus.QUARANTINED,
                        "untrusted content guard rejected section",
                    )
                elif decision.tag in truncated_tags:
                    status, reason = (
                        ContextSelectionStatus.TRUNCATED,
                        "selected section exceeded its available budget",
                    )
                elif decision.tag not in block_tags:
                    status, reason = (
                        ContextSelectionStatus.DROPPED_BUDGET,
                        "selected section received no remaining budget",
                    )
            final_decisions.append(replace(decision, status=status, reason=reason))
        return PromptContext(
            system_context=system_context,
            history=tuple(fitted_history),
            estimated_tokens=estimated,
            dropped_history=dropped,
            truncated_sections=tuple(truncated),
            section_blocks=tuple(section_blocks),
            quarantined_sections=tuple(dict.fromkeys(quarantined)),
            policy_version=self.policy.version,
            node=resolved_node.value,
            route=route,
            selection_trace=tuple(final_decisions),
        )

    def _fit_sections(
        self, sections: Sequence[ContextSection], budget: int
    ) -> Tuple[List[str], int, List[str], List[Tuple[str, str]]]:
        """按最终拼接文本计费，优先装入并恢复调用方原始展示顺序。"""
        selected: Dict[int, str] = {}
        truncated: List[str] = []
        budget = max(0, int(budget))
        ordered = sorted(enumerate(sections), key=lambda item: (-item[1].priority, item[0]))

        def joined(candidate_index: int | None = None, rendered: str = "") -> str:
            candidate = dict(selected)
            if candidate_index is not None:
                candidate[candidate_index] = rendered
            return "\n\n".join(candidate[key] for key in sorted(candidate))

        for index, section in ordered:
            content = str(section.content or "")
            if not content.strip():
                continue
            if budget <= 0:
                truncated.append(section.tag)
                continue
            empty_rendered = section.render("")
            if self.estimator.estimate(joined(index, empty_rendered)) > budget:
                truncated.append(section.tag)
                continue

            low, high = 0, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = section.render(content[:middle])
                if self.estimator.estimate(joined(index, candidate)) <= budget:
                    low = middle
                else:
                    high = middle - 1
            selected[index] = section.render(content[:low])
            if low != len(content):
                truncated.append(section.tag)
        rendered = [selected[index] for index in sorted(selected)]
        blocks = [
            (sections[index].tag, selected[index])
            for index in sorted(selected)
        ]
        used = self.estimator.estimate("\n\n".join(rendered))
        return rendered, used, truncated, blocks

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
