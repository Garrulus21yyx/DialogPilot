"""领域 Worker 内部的有界 ReAct 工具循环。

外层 TaskPlan 拥有任务完整性；本引擎只在单个 Task Owner 内执行
LLM → tool_use → tool_result 循环。工具授权和审批仍由 MCPToolManager
拥有，引擎不能越过该边界直接调用 handler。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.tracing import TraceRecorder, current_trace_id
from core.llm_metrics import create_message
from core.model_policy import ModelProfile, ModelRole
from mcp.tool_manager import MCPToolManager, ToolCallStatus, ToolResult


class ReActStatus(str, Enum):
    """一次 Worker ReAct 执行的闭合终态。"""

    COMPLETED = "completed"
    BLOCKED = "blocked"
    TOOL_ERROR = "tool_error"
    MAX_STEPS = "max_steps"


@dataclass(frozen=True)
class ReActResult:
    """ReAct 最终文本及工具执行证据。"""

    content: str
    status: ReActStatus
    steps: int
    tool_call_ids: Tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    @property
    def success(self) -> bool:
        """只有自然结束且没有被拒绝/失败的工具调用才完成任务。"""
        return self.status is ReActStatus.COMPLETED


@dataclass(frozen=True)
class ParsedToolCall:
    """从供应商 content block 归一化出的工具调用。"""

    call_id: str
    name: str
    arguments: Dict[str, Any]


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

    async def run(
        self,
        *,
        system: str,
        messages: Sequence[Dict[str, Any]],
        agent_type: str,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> ReActResult:
        """循环调用模型和受控工具，直到自然回答或达到闭合失败终态。"""
        tools = self._tool_manager.anthropic_tools_for_agent(agent_type)
        if not tools:
            raise ValueError(f"no tools are available for agent {agent_type}")
        working_messages = [dict(message) for message in messages]
        execution_context = dict(execution_context or {})
        execution_context.setdefault("trace_id", current_trace_id())
        tool_call_ids: List[str] = []
        last_text = ""
        saw_blocked = False
        saw_tool_error = False

        for step in range(1, self._max_steps + 1):
            with self._trace_recorder.span(
                "agent.react.step",
                kind="llm",
                attributes={
                    "agent.type": agent_type,
                    "react.step": step,
                    "react.max_steps": self._max_steps,
                    "tool.count": len(tools),
                },
            ):
                response = await create_message(
                    self._client,
                    self._model_profile,
                    ModelRole.REACT,
                    max_tokens=self._max_tokens,
                    system=system,
                    messages=working_messages,
                    tools=tools,
                )
            blocks, text, tool_calls = self._parse_content(response.content)
            if text:
                last_text = text
            if not tool_calls:
                if not text:
                    return ReActResult(
                        content="模型未产生可发布文本，已转交人工确认。",
                        status=ReActStatus.TOOL_ERROR,
                        steps=step,
                        tool_call_ids=tuple(tool_call_ids),
                        reason="model returned neither text nor tool calls",
                    )
                status = (
                    ReActStatus.BLOCKED if saw_blocked
                    else ReActStatus.TOOL_ERROR if saw_tool_error
                    else ReActStatus.COMPLETED
                )
                return ReActResult(
                    content=text,
                    status=status,
                    steps=step,
                    tool_call_ids=tuple(tool_call_ids),
                    reason=(
                        "one or more tool calls require approval or were denied"
                        if saw_blocked
                        else "one or more tool calls failed"
                        if saw_tool_error
                        else "model completed without further tool calls"
                    ),
                )

            working_messages.append({"role": "assistant", "content": blocks})
            results = await self._execute_calls(
                tool_calls,
                agent_type=agent_type,
                execution_context=execution_context,
            )
            tool_call_ids.extend(result.call_id for result in results)
            saw_blocked = saw_blocked or any(result.status in {
                ToolCallStatus.AWAITING_APPROVAL.value,
                ToolCallStatus.DENIED.value,
            } for result in results)
            saw_tool_error = saw_tool_error or any(
                result.status == ToolCallStatus.ERROR.value for result in results
            )
            working_messages.append({
                "role": "user",
                "content": [self._tool_result_block(result) for result in results],
            })

        return ReActResult(
            content=last_text or "工具调用达到最大步数，已停止自动执行并转交人工确认。",
            status=ReActStatus.MAX_STEPS,
            steps=self._max_steps,
            tool_call_ids=tuple(tool_call_ids),
            reason=f"react exceeded max_steps={self._max_steps}",
        )

    async def _execute_calls(
        self,
        calls: Sequence[ParsedToolCall],
        *,
        agent_type: str,
        execution_context: Dict[str, Any],
    ) -> List[ToolResult]:
        """只读批次可并行；任何潜在写操作都保持稳定串行顺序。"""
        async def execute(call: ParsedToolCall) -> ToolResult:
            return await self._tool_manager.execute_for_agent(
                call.name,
                call.arguments,
                agent_type=agent_type,
                context=execution_context,
                call_id=call.call_id,
            )

        if self._tool_manager.calls_are_parallel_safe([call.name for call in calls]):
            return list(await asyncio.gather(*(execute(call) for call in calls)))
        results = []
        for call in calls:
            results.append(await execute(call))
        return results

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

    @staticmethod
    def _tool_result_block(result: ToolResult) -> Dict[str, Any]:
        """构造与 tool_use_id 配对的 Anthropic tool_result block。"""
        return {
            "type": "tool_result",
            "tool_use_id": result.call_id,
            "content": result.output_for_model,
            "is_error": not result.success,
        }
