"""领域 Worker 内部的有界 ReAct 工具循环。

外层 TaskPlan 拥有任务完整性；本引擎只在单个 Task Owner 内执行
LLM → tool_use → tool_result 循环。工具授权和审批仍由 MCPToolManager
拥有，引擎不能越过该边界直接调用 handler。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from core.tracing import TraceRecorder, current_trace_id
from core.llm_metrics import create_message
from core.model_policy import ModelProfile, ModelRole
from core.provider_context_budget import ProviderContextBudgetExceeded
from agents.tool_result_context import (
    ToolResultContextCompactor,
    render_tool_result_context,
)
from mcp.tool_manager import (
    MCPToolManager,
    ToolCallStatus,
    ToolEffectStatus,
    ToolExecutionReceipt,
    ToolResult,
)


class ReActStatus(str, Enum):
    """一次 Worker ReAct 执行的闭合终态。"""

    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    TOOL_ERROR = "tool_error"
    MAX_STEPS = "max_steps"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class ReActResult:
    """ReAct 最终文本及工具执行证据。"""

    content: str
    status: ReActStatus
    steps: int
    tool_call_ids: Tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
    run_id: str = ""
    pending_approval_call_ids: Tuple[str, ...] = field(default_factory=tuple)
    tool_receipts: Tuple[ToolExecutionReceipt, ...] = field(default_factory=tuple)
    tool_results: Tuple[ToolResult, ...] = field(default_factory=tuple)

    @property
    def success(self) -> bool:
        """只有自然结束且没有被拒绝/失败的工具调用才完成任务。"""
        return self.status is ReActStatus.COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "status": self.status.value,
            "steps": self.steps,
            "tool_call_ids": list(self.tool_call_ids),
            "reason": self.reason,
            "run_id": self.run_id,
            "pending_approval_call_ids": list(self.pending_approval_call_ids),
            "tool_receipts": [item.to_dict() for item in self.tool_receipts],
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ReActResult":
        return cls(
            content=str(payload.get("content") or ""),
            status=ReActStatus(str(payload.get("status") or ReActStatus.TOOL_ERROR.value)),
            steps=int(payload.get("steps") or 0),
            tool_call_ids=tuple(map(str, payload.get("tool_call_ids") or [])),
            reason=str(payload.get("reason") or ""),
            run_id=str(payload.get("run_id") or ""),
            pending_approval_call_ids=tuple(
                map(str, payload.get("pending_approval_call_ids") or [])
            ),
            tool_receipts=tuple(
                ToolExecutionReceipt(**item)
                for item in (payload.get("tool_receipts") or [])
            ),
        )


@dataclass(frozen=True)
class ParsedToolCall:
    """从供应商 content block 归一化出的工具调用。"""

    call_id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class ReActCapability:
    """Host-provided composite capability exposed beside atomic Tools.

    The executor is not serialised into checkpoints. Target only exposes read-only
    Skills here; persistent/high-risk work remains a parent Flow.
    """

    name: str
    description: str
    input_schema: Dict[str, Any]
    execute: Callable[[Dict[str, Any], Dict[str, Any], str], Awaitable[ToolResult]]


class ReActExecutionEngine:
    """Anthropic tool_use 协议上的有限循环执行器。"""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        tool_manager: MCPToolManager,
        model_profile: Optional[ModelProfile] = None,
        trace_recorder: Optional[TraceRecorder] = None,
        max_steps: int = 4,
        max_tokens: int = 1024,
    ):
        if max_steps < 1:
            raise ValueError("max_steps must be positive")
        self._client = client
        self._model_profile = model_profile or ModelProfile(model)
        self._model = self._model_profile.model
        self._tool_manager = tool_manager
        self._trace_recorder = trace_recorder or tool_manager.trace_recorder
        self._max_steps = int(max_steps)
        self._max_tokens = max(64, int(max_tokens))
        self._tool_result_compactor = ToolResultContextCompactor()

    async def run(
        self,
        *,
        system: str,
        messages: Sequence[Dict[str, Any]],
        agent_type: str,
        execution_context: Optional[Dict[str, Any]] = None,
        allowed_tool_ids: Optional[Sequence[str]] = None,
        additional_capabilities: Sequence[ReActCapability] = (),
        control_check: Callable[[str], Awaitable[bool]] | None = None,
    ) -> ReActResult:
        """Legacy bounded model/tool loop pending consumer removal."""
        execution_context = dict(execution_context or {})
        # This constraint is accepted only through the dedicated host argument;
        # never trust a same-named value carried in generic execution context.
        execution_context.pop("allowed_tool_ids", None)
        trusted_allowed_tools = (
            tuple(dict.fromkeys(map(str, allowed_tool_ids)))
            if allowed_tool_ids is not None else None
        )
        tools = self._tool_manager.anthropic_tools_for_agent(
            agent_type,
            description_overrides=dict(execution_context.get("tool_description_overrides") or {}),
            allowed_tool_ids=trusted_allowed_tools,
        )
        capability_by_name = self._capability_map(additional_capabilities, tools)
        tools.extend({
            "name": capability.name,
            "description": capability.description,
            "input_schema": capability.input_schema,
        } for capability in capability_by_name.values())
        if not tools:
            raise ValueError(f"no tools are available for agent {agent_type}")
        working_messages = [dict(message) for message in messages]
        execution_context.setdefault("trace_id", current_trace_id())
        run_id = str(execution_context.get("run_id") or f"react_{uuid.uuid4().hex}")
        execution_context["run_id"] = run_id
        if trusted_allowed_tools is not None:
            execution_context["allowed_tool_ids"] = list(trusted_allowed_tools)
        return await self._continue(
            system=system,
            working_messages=working_messages,
            tools=tools,
            agent_type=agent_type,
            execution_context=execution_context,
            start_step=1,
            tool_call_ids=[],
            last_text="",
            saw_blocked=False,
            saw_tool_error=False,
            tool_receipts=[],
            tool_results=[],
            additional_capabilities=capability_by_name,
            run_id=run_id,
            control_check=control_check,
        )


    async def _continue(
        self,
        *,
        system: str,
        working_messages: List[Dict[str, Any]],
        tools: Sequence[Dict[str, Any]],
        agent_type: str,
        execution_context: Dict[str, Any],
        start_step: int,
        tool_call_ids: List[str],
        last_text: str,
        saw_blocked: bool,
        saw_tool_error: bool,
        tool_receipts: List[ToolExecutionReceipt],
        tool_results: List[ToolResult],
        additional_capabilities: Dict[str, ReActCapability],
        run_id: str,
        control_check: Callable[[str], Awaitable[bool]] | None = None,
    ) -> ReActResult:
        """Execute the remaining legacy in-process loop."""

        for step in range(start_step, self._max_steps + 1):
            if control_check is not None and not await control_check("before_model"):
                result = ReActResult(
                    content="",
                    status=ReActStatus.SUPERSEDED,
                    steps=max(0, step - 1),
                    tool_call_ids=tuple(tool_call_ids),
                    reason="work control was superseded before model execution",
                    run_id=run_id,
                    tool_receipts=tuple(tool_receipts),
                    tool_results=tuple(tool_results),
                )
                return result
            with self._trace_recorder.span(
            "agent.react.step",
            kind="agent",
            attributes={
                "agent.type": agent_type,
                "model": self._model,
                "react.step": step,
                "react.max_steps": self._max_steps,
                "tool.count": len(tools),
            },
            ):
                provider_messages, _ = self._tool_result_compactor.compact(
                    working_messages, preserve_latest_excerpt=True,
                )
                try:
                    response = await create_message(
                        self._client, self._model_profile, ModelRole.REACT,
                        max_tokens=self._max_tokens, system=system,
                        messages=provider_messages, tools=tools,
                    )
                except ProviderContextBudgetExceeded:
                    provider_messages, compacted = (
                        self._tool_result_compactor.compact(
                            working_messages, preserve_latest_excerpt=False,
                        )
                    )
                    if not compacted:
                        raise
                    response = await create_message(
                        self._client, self._model_profile, ModelRole.REACT,
                        max_tokens=self._max_tokens, system=system,
                        messages=provider_messages, tools=tools,
                    )
            blocks, text, tool_calls = self._parse_content(response.content)
            if control_check is not None and not await control_check("after_model"):
                result = ReActResult(
                    content="",
                    status=ReActStatus.SUPERSEDED,
                    steps=step,
                    tool_call_ids=tuple(tool_call_ids),
                    reason="work control was superseded after model execution",
                    run_id=run_id,
                    tool_receipts=tuple(tool_receipts),
                    tool_results=tuple(tool_results),
                )
                return result
            if text:
                last_text = text
            if not tool_calls:
                if not text:
                    result = ReActResult(
                    content="模型未产生可发布文本，已转交人工确认。",
                    status=ReActStatus.TOOL_ERROR,
                    steps=step,
                    tool_call_ids=tuple(tool_call_ids),
                    reason="model returned neither text nor tool calls",
                        run_id=run_id,
                        tool_receipts=tuple(tool_receipts),
                        tool_results=tuple(tool_results),
                    )
                else:
                    status = (
                        ReActStatus.BLOCKED if saw_blocked
                        else ReActStatus.TOOL_ERROR if saw_tool_error
                        else ReActStatus.COMPLETED
                    )
                    result = ReActResult(
                        content=text,
                        status=status,
                        steps=step,
                        tool_call_ids=tuple(tool_call_ids),
                        reason=(
                            "one or more tool calls were denied"
                            if saw_blocked
                            else "one or more tool calls failed"
                            if saw_tool_error
                            else "model completed without further tool calls"
                        ),
                        run_id=run_id,
                        tool_receipts=tuple(tool_receipts),
                        tool_results=tuple(tool_results),
                    )
                return result

            working_messages.append({"role": "assistant", "content": blocks})
            results = await self._execute_calls(
                tool_calls,
                agent_type=agent_type,
                execution_context=execution_context,
                additional_capabilities=additional_capabilities,
                control_check=control_check,
            )
            tool_receipts.extend(
                ToolExecutionReceipt.from_result(result) for result in results
            )
            tool_results.extend(results)
            for result in results:
                if result.call_id not in tool_call_ids:
                    tool_call_ids.append(result.call_id)
            waiting_ids = {
                result.call_id
                for result in results
                if result.status == ToolCallStatus.AWAITING_APPROVAL.value
            }
            if waiting_ids:
                return ReActResult(
                    content="高风险工具调用已暂停，等待宿主审批。",
                    status=ReActStatus.WAITING_APPROVAL,
                    steps=step,
                    tool_call_ids=tuple(tool_call_ids),
                    reason="one or more tool calls require host approval",
                    run_id=run_id,
                    pending_approval_call_ids=tuple(
                        call.call_id for call in tool_calls if call.call_id in waiting_ids
                    ),
                    tool_receipts=tuple(tool_receipts),
                    tool_results=tuple(tool_results),
                )
            saw_blocked = saw_blocked or any(
                result.status == ToolCallStatus.DENIED.value for result in results
            )
            saw_tool_error = saw_tool_error or any(
                result.status in {
                    ToolCallStatus.ERROR.value,
                    ToolCallStatus.TIMEOUT.value,
                    ToolCallStatus.CANCELLED.value,
                }
                for result in results
            )
            working_messages.append({
                "role": "user",
                "content": [
                    self._tool_result_block(result, run_id=run_id)
                    for result in results
                ],
            })

        result = ReActResult(
            content=last_text or "工具调用达到最大步数，已停止自动执行并转交人工确认。",
            status=ReActStatus.MAX_STEPS,
            steps=self._max_steps,
            tool_call_ids=tuple(tool_call_ids),
            reason=f"react exceeded max_steps={self._max_steps}",
            run_id=run_id,
            tool_receipts=tuple(tool_receipts),
            tool_results=tuple(tool_results),
        )
        return result

    async def _execute_calls(
        self,
        calls: Sequence[ParsedToolCall],
        *,
        agent_type: str,
        execution_context: Dict[str, Any],
        approved: bool = False,
        additional_capabilities: Optional[Dict[str, ReActCapability]] = None,
        control_check: Callable[[str], Awaitable[bool]] | None = None,
    ) -> List[ToolResult]:
        """只读批次可并行；任何潜在写操作都保持稳定串行顺序。"""
        async def execute(call: ParsedToolCall) -> ToolResult:
            if control_check is not None and not await control_check("before_tool"):
                return ToolResult(
                    False,
                    None,
                    call.name,
                    error="work control was superseded before tool execution",
                    call_id=call.call_id,
                    status=ToolCallStatus.CANCELLED.value,
                    effect_status=ToolEffectStatus.NONE.value,
                )
            capability = (additional_capabilities or {}).get(call.name)
            if capability is not None:
                result = await capability.execute(
                    call.arguments, execution_context, call.call_id,
                )
                if result.effect_status != ToolEffectStatus.NONE.value:
                    raise ValueError("ReAct composite capabilities must be read-only")
                if control_check is not None and not await control_check("after_tool"):
                    return ToolResult(
                        False, result.data, call.name,
                        error="work control was superseded after tool execution",
                        call_id=call.call_id,
                        status=ToolCallStatus.CANCELLED.value,
                        effect_status=result.effect_status,
                    )
                return result
            result = await self._tool_manager.execute_for_agent(
                call.name,
                call.arguments,
                agent_type=agent_type,
                context=execution_context,
                call_id=call.call_id,
                approved=approved,
                allowed_tool_ids=execution_context.get("allowed_tool_ids"),
            )
            if control_check is not None and not await control_check("after_tool"):
                return ToolResult(
                    False, result.data, call.name,
                    error="work control was superseded after tool execution",
                    call_id=call.call_id,
                    status=ToolCallStatus.CANCELLED.value,
                    effect_status=result.effect_status,
                )
            return result

        if not any(
            call.name in (additional_capabilities or {}) for call in calls
        ) and self._tool_manager.calls_are_parallel_safe([call.name for call in calls]):
            return list(await asyncio.gather(*(execute(call) for call in calls)))
        results = []
        for call in calls:
            results.append(await execute(call))
        return results

    @staticmethod
    def _capability_map(
        capabilities: Sequence[ReActCapability],
        tool_schemas: Sequence[Dict[str, Any]],
    ) -> Dict[str, ReActCapability]:
        by_name: Dict[str, ReActCapability] = {}
        tool_names = {str(item.get("name") or "") for item in tool_schemas}
        for capability in capabilities:
            if not capability.name.strip():
                raise ValueError("ReAct capability name is required")
            if capability.name in tool_names or capability.name in by_name:
                raise ValueError(f"duplicate ReAct capability: {capability.name}")
            if capability.input_schema.get("type") != "object":
                raise ValueError("ReAct capability requires an object input schema")
            by_name[capability.name] = capability
        return by_name


    @classmethod
    def _parse_content(
        cls,
        content: Any,
    ) -> Tuple[List[Dict[str, Any]], str, List[ParsedToolCall]]:
        """把 SDK 对象或字典 content blocks 归一为可回写协议结构。"""
        blocks: List[Dict[str, Any]] = []
        text_parts: List[str] = []
        tool_calls: List[ParsedToolCall] = []
        for raw in content if isinstance(content, list) else [content]:
            block_type = cls._value(raw, "type")
            if block_type == "text":
                text = str(cls._value(raw, "text") or "")
                blocks.append({"type": "text", "text": text})
                if text.strip():
                    text_parts.append(text.strip())
            elif block_type == "tool_use":
                call_id = str(cls._value(raw, "id") or "")
                name = str(cls._value(raw, "name") or "")
                arguments = cls._value(raw, "input")
                arguments = arguments if isinstance(arguments, dict) else {}
                blocks.append({
                    "type": "tool_use",
                    "id": call_id,
                    "name": name,
                    "input": arguments,
                })
                tool_calls.append(ParsedToolCall(call_id, name, arguments))
            elif block_type == "thinking":
                # Thinking + tools 的下一轮必须回传此前 thinking block。
                block = {
                    "type": "thinking",
                    "thinking": str(cls._value(raw, "thinking") or ""),
                }
                signature = cls._value(raw, "signature")
                if signature is not None:
                    block["signature"] = signature
                blocks.append(block)
        return blocks, "\n".join(text_parts).strip(), tool_calls

    @staticmethod
    def _value(block: Any, name: str) -> Any:
        """兼容 Anthropic SDK block 对象和测试/代理端字典。"""
        return block.get(name) if isinstance(block, dict) else getattr(block, name, None)

    def _tool_result_block(
        self, result: ToolResult, *, run_id: str,
    ) -> Dict[str, Any]:
        """构造与 tool_use_id 配对的 Anthropic tool_result block。"""
        return {
            "type": "tool_result",
            "tool_use_id": result.call_id,
            "content": render_tool_result_context(
                result,
            ),
            "is_error": not result.success,
        }
