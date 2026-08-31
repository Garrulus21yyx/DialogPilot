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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from agents.run_store import RunStatus, RunStore, TERMINAL_RUN_STATUSES
from core.tracing import TraceRecorder, current_trace_id
from core.llm_metrics import create_message
from core.model_policy import ModelProfile, ModelRole
from mcp.tool_manager import MCPToolManager, ToolCallStatus, ToolResult


class ReActStatus(str, Enum):
    """一次 Worker ReAct 执行的闭合终态。"""

    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
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
    run_id: str = ""
    pending_approval_call_ids: Tuple[str, ...] = field(default_factory=tuple)

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
        )


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
        run_store: Optional[RunStore] = None,
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
        self._run_store = run_store

    async def run(
        self,
        *,
        system: str,
        messages: Sequence[Dict[str, Any]],
        agent_type: str,
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> ReActResult:
        """创建稳定 Run 并循环调用模型/工具，每个可恢复边界均落盘。"""
        tools = self._tool_manager.anthropic_tools_for_agent(agent_type)
        if not tools:
            raise ValueError(f"no tools are available for agent {agent_type}")
        working_messages = [dict(message) for message in messages]
        execution_context = dict(execution_context or {})
        execution_context.setdefault("trace_id", current_trace_id())
        run_id = str(execution_context.get("run_id") or f"react_{uuid.uuid4().hex}")
        execution_context["run_id"] = run_id
        checkpoint_version: Optional[int] = None
        if self._run_store is not None:
            checkpoint = self._run_store.create(
                run_id=run_id,
                request_id=str(execution_context.get("request_id") or ""),
                user_id=str(execution_context.get("user_id") or ""),
                conv_id=str(execution_context.get("conv_id") or ""),
                agent_type=agent_type,
                task_id=str(execution_context.get("task_id") or ""),
                bundle_version=str(execution_context.get("bundle_version") or "unversioned"),
                system=system,
                messages=tuple(working_messages),
                execution_context=execution_context,
                max_steps=self._max_steps,
            )
            checkpoint_version = checkpoint.version
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
            run_id=run_id,
            checkpoint_version=checkpoint_version,
        )

    async def resume(
        self,
        run_id: str,
        *,
        user_id: str,
        approved: bool,
        actor: str,
    ) -> ReActResult:
        """用持久 checkpoint 恢复一批等待审批的工具调用。"""
        if self._run_store is None:
            raise RuntimeError("react run storage is not configured")
        checkpoint = self._run_store.acquire_waiting(run_id, user_id)
        if checkpoint.status in TERMINAL_RUN_STATUSES:
            if checkpoint.result:
                return ReActResult.from_dict(checkpoint.result)
            return ReActResult(
                content="审批窗口已过期，未执行工具操作。",
                status=ReActStatus.BLOCKED,
                steps=checkpoint.step,
                tool_call_ids=checkpoint.tool_call_ids,
                reason=f"run is {checkpoint.status.value}",
                run_id=run_id,
            )
        pending = dict(checkpoint.pending or {})
        if pending.get("phase") not in {"waiting_approval", "approved_executing"}:
            raise ValueError("run checkpoint has no resumable approval batch")
        calls = [
            ParsedToolCall(
                str(item.get("call_id") or ""),
                str(item.get("name") or ""),
                dict(item.get("arguments") or {}),
            )
            for item in (pending.get("calls") or [])
        ]
        runtime = dict(checkpoint.runtime)
        recovering_approved = pending.get("phase") == "approved_executing"
        if recovering_approved and not approved:
            raise ValueError("an approved execution cannot be changed to denied during recovery")
        runtime["approval_actor"] = str(actor)[:200]
        runtime["approval_decision"] = "approved" if approved else "denied"
        if not approved:
            result = ReActResult(
                content="工具调用已被审批人拒绝，未执行副作用。",
                status=ReActStatus.BLOCKED,
                steps=checkpoint.step,
                tool_call_ids=checkpoint.tool_call_ids,
                reason="host denied pending tool calls",
                run_id=run_id,
            )
            self._run_store.checkpoint(
                run_id=run_id,
                expected_version=checkpoint.version,
                status=RunStatus.BLOCKED,
                messages=checkpoint.messages,
                runtime=runtime,
                step=checkpoint.step,
                tool_call_ids=checkpoint.tool_call_ids,
                pending={},
                result=result.to_dict(),
            )
            return result

        if not recovering_approved:
            pending["phase"] = "approved_executing"
            checkpoint = self._run_store.checkpoint(
                run_id=run_id,
                expected_version=checkpoint.version,
                status=RunStatus.RUNNING,
                messages=checkpoint.messages,
                runtime=runtime,
                step=checkpoint.step,
                tool_call_ids=checkpoint.tool_call_ids,
                pending=pending,
            )

        execution_context = dict(checkpoint.execution_context)
        execution_context["run_id"] = run_id
        approved_results = await self._execute_calls(
            calls,
            agent_type=checkpoint.agent_type,
            execution_context=execution_context,
            approved=True,
        )
        completed_results = {
            str(item.get("call_id") or ""): self._tool_result_from_dict(item)
            for item in (pending.get("completed_results") or [])
        }
        completed_results.update({result.call_id: result for result in approved_results})
        ordered_results = [
            completed_results[call_id]
            for call_id in pending.get("ordered_call_ids") or []
            if call_id in completed_results
        ]
        working_messages = [dict(message) for message in checkpoint.messages]
        working_messages.append({
            "role": "user",
            "content": [self._tool_result_block(result) for result in ordered_results],
        })
        saw_blocked = bool(runtime.get("saw_blocked")) or any(
            result.status == ToolCallStatus.DENIED.value for result in ordered_results
        )
        saw_tool_error = bool(runtime.get("saw_tool_error")) or any(
            result.status in {
                ToolCallStatus.ERROR.value,
                ToolCallStatus.TIMEOUT.value,
                ToolCallStatus.CANCELLED.value,
            }
            for result in ordered_results
        )
        runtime.update({"saw_blocked": saw_blocked, "saw_tool_error": saw_tool_error})
        checkpoint = self._run_store.checkpoint(
            run_id=run_id,
            expected_version=checkpoint.version,
            status=RunStatus.RUNNING,
            messages=tuple(working_messages),
            runtime=runtime,
            step=checkpoint.step,
            tool_call_ids=checkpoint.tool_call_ids,
            pending={},
        )
        tools = self._tool_manager.anthropic_tools_for_agent(checkpoint.agent_type)
        return await self._continue(
            system=checkpoint.system,
            working_messages=working_messages,
            tools=tools,
            agent_type=checkpoint.agent_type,
            execution_context=execution_context,
            start_step=checkpoint.step + 1,
            tool_call_ids=list(checkpoint.tool_call_ids),
            last_text=str(runtime.get("last_text") or ""),
            saw_blocked=saw_blocked,
            saw_tool_error=saw_tool_error,
            run_id=run_id,
            checkpoint_version=checkpoint.version,
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
        run_id: str,
        checkpoint_version: Optional[int],
    ) -> ReActResult:
        """从一个已落盘步骤继续；新 Run 和 resume 共用同一执行代数。"""
        current_version = checkpoint_version

        try:
            for step in range(start_step, self._max_steps + 1):
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
                        result = ReActResult(
                        content="模型未产生可发布文本，已转交人工确认。",
                        status=ReActStatus.TOOL_ERROR,
                        steps=step,
                        tool_call_ids=tuple(tool_call_ids),
                        reason="model returned neither text nor tool calls",
                            run_id=run_id,
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
                        )
                    self._persist_terminal(result, tuple(working_messages), current_version)
                    return result

                working_messages.append({"role": "assistant", "content": blocks})
                serialized_calls = [self._call_to_dict(call) for call in tool_calls]
                current_version = self._persist_checkpoint(
                    run_id=run_id,
                    version=current_version,
                    status=RunStatus.RUNNING,
                    messages=tuple(working_messages),
                    runtime={
                        "last_text": last_text,
                        "saw_blocked": saw_blocked,
                        "saw_tool_error": saw_tool_error,
                    },
                    step=step,
                    tool_call_ids=tuple(tool_call_ids),
                    pending={"phase": "executing", "calls": serialized_calls},
                )
                results = await self._execute_calls(
                    tool_calls,
                    agent_type=agent_type,
                    execution_context=execution_context,
                )
                for result in results:
                    if result.call_id not in tool_call_ids:
                        tool_call_ids.append(result.call_id)
                waiting_ids = {
                    result.call_id
                    for result in results
                    if result.status == ToolCallStatus.AWAITING_APPROVAL.value
                }
                if waiting_ids:
                    runtime = {
                        "last_text": last_text,
                        "saw_blocked": saw_blocked,
                        "saw_tool_error": saw_tool_error,
                    }
                    pending = {
                        "phase": "waiting_approval",
                        "calls": [
                            self._call_to_dict(call)
                            for call in tool_calls
                            if call.call_id in waiting_ids
                        ],
                        "ordered_call_ids": [call.call_id for call in tool_calls],
                        "completed_results": [
                            self._tool_result_to_dict(result)
                            for result in results
                            if result.call_id not in waiting_ids
                        ],
                    }
                    current_version = self._persist_checkpoint(
                        run_id=run_id,
                        version=current_version,
                        status=RunStatus.WAITING_APPROVAL,
                        messages=tuple(working_messages),
                        runtime=runtime,
                        step=step,
                        tool_call_ids=tuple(tool_call_ids),
                        pending=pending,
                    )
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
                    "content": [self._tool_result_block(result) for result in results],
                })
                current_version = self._persist_checkpoint(
                    run_id=run_id,
                    version=current_version,
                    status=RunStatus.RUNNING,
                    messages=tuple(working_messages),
                    runtime={
                        "last_text": last_text,
                        "saw_blocked": saw_blocked,
                        "saw_tool_error": saw_tool_error,
                    },
                    step=step,
                    tool_call_ids=tuple(tool_call_ids),
                    pending={},
                )

            result = ReActResult(
                content=last_text or "工具调用达到最大步数，已停止自动执行并转交人工确认。",
                status=ReActStatus.MAX_STEPS,
                steps=self._max_steps,
                tool_call_ids=tuple(tool_call_ids),
                reason=f"react exceeded max_steps={self._max_steps}",
                run_id=run_id,
            )
            self._persist_terminal(result, tuple(working_messages), current_version)
            return result
        except asyncio.CancelledError:
            if self._run_store is not None and current_version is not None:
                cancelled = ReActResult(
                    content="",
                    status=ReActStatus.TOOL_ERROR,
                    steps=max(0, start_step - 1),
                    tool_call_ids=tuple(tool_call_ids),
                    reason="react run was cancelled",
                    run_id=run_id,
                )
                try:
                    self._run_store.checkpoint(
                        run_id=run_id,
                        expected_version=current_version,
                        status=RunStatus.CANCELLED,
                        messages=tuple(working_messages),
                        runtime={"last_text": last_text},
                        step=cancelled.steps,
                        tool_call_ids=tuple(tool_call_ids),
                        pending={},
                        result=cancelled.to_dict(),
                    )
                except Exception:
                    pass
            raise

    async def _execute_calls(
        self,
        calls: Sequence[ParsedToolCall],
        *,
        agent_type: str,
        execution_context: Dict[str, Any],
        approved: bool = False,
    ) -> List[ToolResult]:
        """只读批次可并行；任何潜在写操作都保持稳定串行顺序。"""
        async def execute(call: ParsedToolCall) -> ToolResult:
            return await self._tool_manager.execute_for_agent(
                call.name,
                call.arguments,
                agent_type=agent_type,
                context=execution_context,
                call_id=call.call_id,
                approved=approved,
            )

        if self._tool_manager.calls_are_parallel_safe([call.name for call in calls]):
            return list(await asyncio.gather(*(execute(call) for call in calls)))
        results = []
        for call in calls:
            results.append(await execute(call))
        return results

    def _persist_checkpoint(
        self,
        *,
        run_id: str,
        version: Optional[int],
        status: RunStatus,
        messages: Tuple[Dict[str, Any], ...],
        runtime: Dict[str, Any],
        step: int,
        tool_call_ids: Tuple[str, ...],
        pending: Dict[str, Any],
    ) -> Optional[int]:
        if self._run_store is None or version is None:
            return version
        return self._run_store.checkpoint(
            run_id=run_id,
            expected_version=version,
            status=status,
            messages=messages,
            runtime=runtime,
            step=step,
            tool_call_ids=tool_call_ids,
            pending=pending,
        ).version

    def _persist_terminal(
        self,
        result: ReActResult,
        messages: Tuple[Dict[str, Any], ...],
        version: Optional[int],
    ) -> None:
        if self._run_store is None or version is None:
            return
        status = {
            ReActStatus.COMPLETED: RunStatus.COMPLETED,
            ReActStatus.BLOCKED: RunStatus.BLOCKED,
            ReActStatus.TOOL_ERROR: RunStatus.TOOL_ERROR,
            ReActStatus.MAX_STEPS: RunStatus.MAX_STEPS,
            ReActStatus.WAITING_APPROVAL: RunStatus.WAITING_APPROVAL,
        }[result.status]
        self._run_store.checkpoint(
            run_id=result.run_id,
            expected_version=version,
            status=status,
            messages=messages,
            runtime={},
            step=result.steps,
            tool_call_ids=result.tool_call_ids,
            pending={},
            result=result.to_dict(),
        )

    @staticmethod
    def _call_to_dict(call: ParsedToolCall) -> Dict[str, Any]:
        return {"call_id": call.call_id, "name": call.name, "arguments": call.arguments}

    @staticmethod
    def _tool_result_to_dict(result: ToolResult) -> Dict[str, Any]:
        return {
            key: getattr(result, key)
            for key in ToolResult.__dataclass_fields__
        }

    @staticmethod
    def _tool_result_from_dict(payload: Dict[str, Any]) -> ToolResult:
        return ToolResult(**{
            key: payload.get(key)
            for key in ToolResult.__dataclass_fields__
            if key in payload
        })

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
