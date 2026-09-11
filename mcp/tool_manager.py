"""
亮点：MCP 工具调用框架

核心问题：工具调用出错（检索不全、召回不好）怎么优化？

本模块的答案：
  1. 查询改写（Query Rewriting）—— 用 LLM 把用户原始问题扩写成多个角度的子查询，
     再合并去重，解决"召回不全"问题。
  2. 结果重排（Reranking）—— 对召回结果用 LLM 打分，按相关性重新排序，
     解决"召回不好/排序差"问题。
  3. 熔断器（Circuit Breaker）—— 连续失败超阈值时自动断开，防止雪崩。
  4. 结果缓存（TTL Cache）—— 相同参数直接返回缓存，减少重复调用。
  5. 降级策略（Fallback）—— 工具不可用时返回有意义的降级结果。
"""
import asyncio
import hashlib
import inspect
import json
import logging
import time
import uuid
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Collection, Deque, Dict, List, Optional, Tuple

from anthropic import AsyncAnthropic

from core.input_security import UntrustedContentGuard
from core.model_policy import ModelProfile
from core.tracing import TraceRecorder, current_trace_id, trace_scope
from core.identity import InvocationKey, OperationKey
from mcp.query_transformer import QueryTransformer
from mcp.read_reuse import ReadReusePolicy
from mcp.result_reranker import RerankResult, ResultReranker, candidates_from_items

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

class CircuitState(Enum):
    """熔断器的三态生命周期。"""
    CLOSED    = "closed"     # 正常
    OPEN      = "open"       # 熔断，拒绝请求
    HALF_OPEN = "half_open"  # 探测恢复


class ToolRisk(str, Enum):
    """工具副作用风险；高风险在默认模式下一律要求宿主批准。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalMode(str, Enum):
    """由宿主配置的工具审批策略，模型不能修改。"""

    DEFAULT = "default"
    REQUIRE_ALL = "require_all"
    AUTO_APPROVE = "auto_approve"


class ToolCallStatus(str, Enum):
    """一次受控工具调用的闭合状态集合。"""

    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DENIED = "denied"
    UNTRUSTED_OUTPUT = "untrusted_output"
    REJECTED = "rejected"


class ToolRejected(ValueError):
    """An authoritative endpoint rejected the request without committing it.

    Adapters retain the endpoint's actionable feedback; this is not a transport
    outage and must not open a circuit or trigger an unchanged automatic retry.
    """

    def __init__(self, message: str, *, data=None):
        super().__init__(message)
        self.data = data


class ToolEffectStatus(str, Enum):
    """业务副作用的可证明状态，与调用终态分开建模。"""

    NONE = "none"
    COMMITTED = "committed"
    NOT_COMMITTED = "not_committed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True)
class ToolEffectReceipt:
    """由业务 handler 返回的提交回执；manager 不自行猜测写入结果。"""

    data: Any
    effect_status: ToolEffectStatus
    receipt_id: str = ""


@dataclass(frozen=True)
class ToolObservationEnvelope:
    """Private provenance attached to a read without changing model-visible data."""

    data: Any
    source_call_ids: tuple[str, ...]


@dataclass
class ToolResult:
    """一次工具调用的统一结果，包含缓存、延迟和重排证据。"""
    success:        bool
    data:           Any
    tool_name:      str
    error:          Optional[str] = None
    cached:         bool = False
    latency_ms:     float = 0.0
    reranked:       bool = False   # 是否经过重排
    call_id:         str = ""
    trace_id:        str = ""
    status:          str = ""
    output_for_model: str = ""
    effect_status:   str = ToolEffectStatus.NONE.value
    receipt_id:      str = ""
    authority:       str = ""
    output_schema_version: str = ""
    receipt_schema_version: str = ""
    query_ref: str = ""
    observation_started_at: datetime | None = None
    observed_at: datetime | None = None
    causal_source_call_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolExecutionReceipt:
    """Typed execution evidence returned to the caller, independent of audit logs."""

    call_id: str
    tool_name: str
    status: str
    authority: str
    output_schema_version: str
    receipt_schema_version: str
    effect_status: str
    receipt_id: str = ""

    @classmethod
    def from_result(cls, result: ToolResult) -> "ToolExecutionReceipt":
        return cls(
            call_id=result.call_id,
            tool_name=result.tool_name,
            status=result.status,
            authority=result.authority,
            output_schema_version=result.output_schema_version,
            receipt_schema_version=result.receipt_schema_version,
            effect_status=result.effect_status,
            receipt_id=result.receipt_id,
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "authority": self.authority,
            "output_schema_version": self.output_schema_version,
            "receipt_schema_version": self.receipt_schema_version,
            "effect_status": self.effect_status,
            "receipt_id": self.receipt_id,
        }


@dataclass(frozen=True)
class ToolAuditRecord:
    """不含原始敏感参数/结果的工具调用审计记录。"""

    trace_id: str
    call_id: str
    request_id: str
    agent_type: str
    tool_name: str
    status: ToolCallStatus
    risk: ToolRisk
    read_only: bool
    params_hash: str
    params_summary: str
    result_summary: str
    approved: bool
    started_at: str
    latency_ms: float
    error: str = ""
    effect_status: ToolEffectStatus = ToolEffectStatus.NONE
    receipt_id: str = ""
    invocation_key: str = ""
    operation_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """生成稳定 API/Trace 投影。"""
        return {
            "trace_id": self.trace_id,
            "call_id": self.call_id,
            "request_id": self.request_id,
            "agent_type": self.agent_type,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "risk": self.risk.value,
            "read_only": self.read_only,
            "params_hash": self.params_hash,
            "params_summary": self.params_summary,
            "result_summary": self.result_summary,
            "approved": self.approved,
            "started_at": self.started_at,
            "latency_ms": round(self.latency_ms, 3),
            "error": self.error,
            "effect_status": self.effect_status.value,
            "receipt_id": self.receipt_id,
            "invocation_key": self.invocation_key,
            "operation_key": self.operation_key,
        }


@dataclass
class ToolStats:
    """工具运行时统计，供 Monitor 读取。"""
    total:              int = 0
    success:            int = 0
    failed:             int = 0
    total_latency_ms:   float = 0.0
    consecutive_fails:  int = 0

    @property
    def success_rate(self) -> float:
        """无样本时返回健康初始值 1.0，避免冷启动误惩罚。"""
        return self.success / self.total if self.total else 1.0

    @property
    def avg_latency_ms(self) -> float:
        """返回已记录调用的平均耗时。"""
        return self.total_latency_ms / self.total if self.total else 0.0


# ── 熔断器 ────────────────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    三态熔断器：CLOSED → OPEN → HALF_OPEN → CLOSED

    连续失败 failure_threshold 次后打开；
    打开 recovery_s 秒后进入 HALF_OPEN 探测；
    探测成功则关闭，失败则重新打开。
    """

    def __init__(self, failure_threshold: int = 5, recovery_s: float = 60.0):
        """配置连续失败阈值和 OPEN 状态恢复等待时间。"""
        self.threshold   = failure_threshold
        self.recovery_s  = recovery_s
        self.state       = CircuitState.CLOSED
        self.fail_count  = 0
        self.opened_at:  Optional[float] = None

    def allow(self) -> bool:
        """判断本次调用能否执行，并在恢复窗口后进入 HALF_OPEN 探测。"""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.opened_at >= self.recovery_s:  # type: ignore
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN：放行一次探测

    def record_success(self) -> None:
        """成功调用关闭熔断器并清空连续失败计数。"""
        self.fail_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """累计连续失败；达到阈值后记录打开时间并拒绝后续调用。"""
        self.fail_count += 1
        if self.fail_count >= self.threshold:
            self.state     = CircuitState.OPEN
            self.opened_at = time.monotonic()
            logger.warning(f"熔断器打开（连续失败 {self.fail_count} 次）")


# ── 工具定义 ──────────────────────────────────────────────────────────────────

@dataclass
class Tool:
    """工具静态合同及其由运行时拥有的统计、熔断状态。"""
    name:        str
    description: str
    handler:     Callable                    # async (params, context) -> Any
    schema:      Dict[str, Any]              # JSON Schema
    cache_ttl:   float = 0.0                 # 0 = 不缓存
    timeout_s:   float = 30.0
    supports_rerank: bool = False            # 是否支持结果重排
    fallback:    Optional[Callable] = None    # sync/async (params, context, error) -> Any
    allowed_agents: Tuple[str, ...] = ("*",) # ReAct 可发现/执行该工具的 Agent allowlist
    risk: ToolRisk = ToolRisk.LOW
    read_only: bool = True
    requires_approval: bool = False
    authority: str = ""
    manifest_version: str = ""
    output_schema_version: str = ""
    receipt_schema_version: str = ""
    preconditions: Tuple[str, ...] = ()
    idempotency: str = "not_declared"
    retry_policy: str = "not_declared"
    typed_outcomes: Tuple[str, ...] = ()
    output_fields: Tuple[str, ...] = ()

    schema_factory: Optional[Callable] = None
    schema_factory_version: str = ""
    task_read_reuse: Optional["ReadReusePolicy"] = None

    def input_schema(self, context=None):
        return self.schema_factory(dict(context or {})) if self.schema_factory else self.schema

    # 运行时状态（不参与构造）
    stats:   ToolStats    = field(default_factory=ToolStats, init=False)
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker, init=False)


# ── MCP 工具管理器 ────────────────────────────────────────────────────────────

class MCPToolManager:
    """
    MCP 工具调用框架。

    核心优化链路（针对检索类工具）：
      用户查询 → 查询改写（多角度子查询）→ 并行召回 → 结果重排 → 返回 Top-K
    """

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        model: str = "claude-3-5-sonnet-20241022",
        *,
        approval_mode: ApprovalMode = ApprovalMode.DEFAULT,
        trace_recorder: Optional[TraceRecorder] = None,
        max_audit_records: int = 2000,
        rewrite_model_profile: Optional[ModelProfile] = None,
        rerank_model_profile: Optional[ModelProfile] = None,
    ):
        """创建模型客户端以及进程内工具注册表和 TTL 缓存。"""
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncAnthropic(**kwargs)
        self._rewrite_model_profile = rewrite_model_profile or ModelProfile(model)
        self._rerank_model_profile = rerank_model_profile or self._rewrite_model_profile
        self._query_transformer = QueryTransformer(self._client, self._rewrite_model_profile)
        self._result_reranker = ResultReranker(self._client, self._rerank_model_profile)
        self._model  = self._rewrite_model_profile.model
        self._tools: Dict[str, Tool] = {}
        self._cache: Dict[str, tuple] = {}   # key → (result, expire_at, reranked)
        self._approval_mode = ApprovalMode(approval_mode)
        self._trace_recorder = trace_recorder or TraceRecorder()
        self._audit: Deque[ToolAuditRecord] = deque(maxlen=max(1, int(max_audit_records)))

    # ── 注册 / 注销 ───────────────────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        """按名称注册或替换工具定义。"""
        if not tool.name.strip():
            raise ValueError("tool name must not be empty")
        if not tool.allowed_agents:
            raise ValueError("tool allowed_agents must not be empty")
        if tool.task_read_reuse is not None:
            if not isinstance(tool.task_read_reuse, ReadReusePolicy) or not tool.read_only:
                raise ValueError("task read reuse requires a read-only tool and explicit policy")
        if tool.schema_factory and (not tool.schema_factory_version or tool.cache_ttl > 0):
            raise ValueError("runtime schema requires a version and uncached execution")
        self._tools[tool.name] = tool
        logger.info(f"注册工具: {tool.name}")

    def unregister(self, name: str) -> None:
        """幂等移除工具定义。"""
        self._tools.pop(name, None)

    def tools_for_agent(
        self,
        agent_type: str,
        *,
        allowed_tool_ids: Optional[Collection[str]] = None,
    ) -> List[Tool]:
        """返回 Agent 与当前 WorkItem 能力包络的交集。

        ``allowed_tool_ids`` 是宿主编译出的可信约束，不属于模型输入。显式
        包络含未知或 Agent 无权使用的工具时直接拒绝，避免配置错误被静默裁剪。
        """
        normalized = str(getattr(agent_type, "value", agent_type))
        agent_tools = [
            tool for tool in self._tools.values()
            if "*" in tool.allowed_agents or normalized in tool.allowed_agents
        ]
        if allowed_tool_ids is None:
            return agent_tools
        requested = tuple(dict.fromkeys(map(str, allowed_tool_ids)))
        agent_names = {tool.name for tool in agent_tools}
        invalid = sorted(set(requested) - agent_names)
        if invalid:
            raise ValueError(
                f"work-item tool envelope is invalid for {normalized}: {invalid}"
            )
        requested_set = set(requested)
        return [tool for tool in agent_tools if tool.name in requested_set]

    def anthropic_tools_for_agent(
        self,
        agent_type: str,
        *,
        description_overrides: Optional[Dict[str, str]] = None,
        allowed_tool_ids: Optional[Collection[str]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """投影允许工具；Bundle 只能覆盖描述，不能改变 schema 或权限。"""
        overrides = dict(description_overrides or {})
        return [{
            "name": tool.name,
            "description": str(overrides.get(tool.name) or tool.description),
            "input_schema": tool.input_schema(context),
        } for tool in self.tools_for_agent(
            agent_type, allowed_tool_ids=allowed_tool_ids,
        )]

    def registry_fingerprint(self, description_overrides: Optional[Dict[str, str]] = None) -> str:
        """哈希实际模型可见合同及安全属性，不包含 handler 和运行统计。"""
        overrides = dict(description_overrides or {})
        rows = [{
            "name": tool.name,
            "description": str(overrides.get(tool.name) or tool.description),
            "schema": tool.schema,
            **({"schema_factory_version": tool.schema_factory_version} if tool.schema_factory else {}),
            "allowed_agents": sorted(tool.allowed_agents),
            "risk": tool.risk.value,
            "read_only": tool.read_only,
            "requires_approval": tool.requires_approval,
            "authority": tool.authority,
            "manifest_version": tool.manifest_version,
            "output_schema_version": tool.output_schema_version,
            "receipt_schema_version": tool.receipt_schema_version,
            "preconditions": sorted(tool.preconditions),
            "idempotency": tool.idempotency,
            "retry_policy": tool.retry_policy,
            "typed_outcomes": sorted(tool.typed_outcomes),
            "output_fields": sorted(tool.output_fields),
            **({"task_read_reuse": {"max_age_seconds": tool.task_read_reuse.max_age_seconds}}
               if tool.task_read_reuse else {}),
        } for tool in sorted(self._tools.values(), key=lambda item: item.name)]
        return hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def registered_tool_names(self) -> Tuple[str, ...]:
        """返回注册身份快照，供 Bundle 激活校验，不暴露 handler。"""
        return tuple(sorted(self._tools))

    @property
    def registered_tools(self) -> Tuple[Tool, ...]:
        """Return an immutable snapshot for owner-level manifest validation."""
        return tuple(self._tools[name] for name in sorted(self._tools))

    @property
    def llm_client(self) -> Any:
        """Shared provider client for adjacent prompt owners in the same runtime."""
        return self._client

    def calls_are_parallel_safe(self, tool_names: List[str]) -> bool:
        """只有全部已注册工具都是只读时，ReAct 才能并行派发。"""
        tools = [self._tools.get(name) for name in tool_names]
        return bool(tools) and all(tool is not None and tool.read_only for tool in tools)

    async def execute_for_agent(
        self,
        name: str,
        params: Dict[str, Any],
        *,
        agent_type: str,
        context: Optional[Dict[str, Any]] = None,
        approved: bool = False,
        call_id: Optional[str] = None,
        allowed_tool_ids: Optional[Collection[str]] = None,
        reuse_result: Optional[ToolResult] = None,
        use_cache: bool = True,
    ) -> ToolResult:
        """在身份、审批、审计和 Trace 边界内执行一次 Agent 工具调用。

        ``approved`` 只能由宿主调用方传入，不属于 LLM tool schema，模型无法
        通过伪造参数绕过审批。
        """
        context = dict(context or {})
        params = self._strip_control_params(params)
        normalized_agent = str(getattr(agent_type, "value", agent_type))
        resolved_call_id = str(call_id or uuid.uuid4().hex)
        # Agent 身份与调用 ID 由执行边界覆盖，不能信任调用方 context 中的同名值。
        context["agent_type"] = normalized_agent
        context["tool_call_id"] = resolved_call_id
        invocation_key = str(context.get("invocation_key") or "").strip()
        if invocation_key:
            context["operation_key"] = str(OperationKey.build(
                InvocationKey(invocation_key),
                owner=f"tool:{name}",
                operation="execute",
                subject=resolved_call_id,
            ))
        trace_id = str(context.get("trace_id") or current_trace_id() or uuid.uuid4().hex)
        request_id = str(context.get("request_id") or "")
        started_iso = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        tool = self._tools.get(name)

        if tool is None:
            return self._finish_controlled_call(
                result=ToolResult(False, None, name, error="工具不存在"),
                tool=None,
                agent_type=normalized_agent,
                params=params,
                context=context,
                trace_id=trace_id,
                call_id=resolved_call_id,
                request_id=request_id,
                started_iso=started_iso,
                started=started,
                status=ToolCallStatus.DENIED,
                approved=False,
            )

        if allowed_tool_ids is not None and name not in set(map(str, allowed_tool_ids)):
            return self._finish_controlled_call(
                result=ToolResult(False, None, name, error="工具不在当前 WorkItem 能力包络内"),
                tool=tool,
                agent_type=normalized_agent,
                params=params,
                context=context,
                trace_id=trace_id,
                call_id=resolved_call_id,
                request_id=request_id,
                started_iso=started_iso,
                started=started,
                status=ToolCallStatus.DENIED,
                approved=False,
            )

        if "*" not in tool.allowed_agents and normalized_agent not in tool.allowed_agents:
            return self._finish_controlled_call(
                result=ToolResult(False, None, name, error="Agent 无权调用该工具"),
                tool=tool,
                agent_type=normalized_agent,
                params=params,
                context=context,
                trace_id=trace_id,
                call_id=resolved_call_id,
                request_id=request_id,
                started_iso=started_iso,
                started=started,
                status=ToolCallStatus.DENIED,
                approved=False,
            )

        needs_approval = self._requires_approval(tool)
        if needs_approval and not approved:
            return self._finish_controlled_call(
                result=ToolResult(False, None, name, error="工具调用等待宿主审批"),
                tool=tool,
                agent_type=normalized_agent,
                params=params,
                context=context,
                trace_id=trace_id,
                call_id=resolved_call_id,
                request_id=request_id,
                started_iso=started_iso,
                started=started,
                status=ToolCallStatus.AWAITING_APPROVAL,
                approved=False,
            )

        span_attributes = {
            "tool.name": name,
            "tool.agent": normalized_agent,
            "tool.risk": tool.risk.value,
            "tool.call_id": resolved_call_id,
        }
        try:
            scope = nullcontext(trace_id) if current_trace_id() == trace_id else trace_scope(trace_id)
            with scope:
                with self._trace_recorder.span(
                    f"tool.{name}", kind="tool", attributes=span_attributes
                ):
                    if reuse_result is not None:
                        from copy import deepcopy
                        if (tool.task_read_reuse is None or not tool.read_only
                                or not reuse_result.success or reuse_result.tool_name != name):
                            raise ValueError("invalid task read reuse")
                        self._validate_params(tool, params, context)
                        result = deepcopy(reuse_result)
                        result.cached = True
                        tool.stats.total += 1
                        tool.stats.success += 1
                    else:
                        result = await self.call(name, params, {
                            **dict(context or {}), "_agent_type": normalized_agent,
                        }, use_cache=use_cache)
            status = (
                ToolCallStatus(result.status)
                if result.status
                else ToolCallStatus.SUCCESS if result.success else ToolCallStatus.ERROR
            )
        except asyncio.CancelledError:
            # 取消必须留下唯一终态审计，但仍向调用方传播取消语义。
            result = ToolResult(
                False,
                None,
                name,
                error="调用被取消",
                effect_status=(
                    ToolEffectStatus.NONE.value
                    if tool.read_only
                    else ToolEffectStatus.OUTCOME_UNKNOWN.value
                ),
            )
            self._finish_controlled_call(
                result=result,
                tool=tool,
                agent_type=normalized_agent,
                params=params,
                context=context,
                trace_id=trace_id,
                call_id=resolved_call_id,
                request_id=request_id,
                started_iso=started_iso,
                started=started,
                status=ToolCallStatus.CANCELLED,
                approved=approved or not needs_approval,
            )
            raise
        except Exception as exc:  # call() 应闭合异常，此处保护未来适配器。
            result = ToolResult(False, None, name, error=f"{type(exc).__name__}: {exc}")
            status = ToolCallStatus.ERROR
        return self._finish_controlled_call(
            result=result,
            tool=tool,
            agent_type=normalized_agent,
            params=params,
            context=context,
            trace_id=trace_id,
            call_id=resolved_call_id,
            request_id=request_id,
            started_iso=started_iso,
            started=started,
            status=status,
            approved=approved or not needs_approval,
        )

    def audit_records(
        self,
        *,
        trace_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[ToolAuditRecord]:
        """读取工具审计快照，可按 TraceId 关联一次请求。"""
        records = list(self._audit)
        if trace_id:
            records = [record for record in records if record.trace_id == trace_id]
        return records[-max(0, int(limit)):]

    @property
    def trace_recorder(self) -> TraceRecorder:
        """返回工具运行时使用的唯一 Trace Recorder。"""
        return self._trace_recorder

    # ── 核心调用 ──────────────────────────────────────────────────────────────

    async def call(
        self,
        name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        *,
        use_cache: bool = True,
        rerank_top_k: int = 0,          # >0 时对结果重排，取 Top-K
    ) -> ToolResult:
        """
        调用工具，完整执行链：
          缓存检查 → 熔断检查 → 参数校验 → 执行（含超时）→ 可选重排 → 缓存写入
        """
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(success=False, data=None, tool_name=name, error=f"工具不存在: {name}")

        cache_rerank_top_k = rerank_top_k if rerank_top_k > 0 and tool.supports_rerank else 0

        # 同一 Bundle 内可复用；检索策略变化后不得误用上一版本结果。
        cache_scope = str((context or {}).get("cache_scope") or "")[:128]
        if use_cache and tool.cache_ttl > 0:
            cached = self._get_cache(name, params, cache_rerank_top_k, cache_scope)
            if cached is not None:
                cached_data, cached_reranked, cached_start, cached_end = cached
                tool.stats.total += 1
                tool.stats.success += 1
                return ToolResult(
                    success=True,
                    data=cached_data,
                    tool_name=name,
                    cached=True,
                    reranked=cached_reranked,
                    observation_started_at=cached_start,
                    observed_at=cached_end,
                )

        # 熔断检查
        if not tool.breaker.allow():
            error = f"工具熔断中: {name}，请稍后重试"
            return await self._fallback_result(tool, params, context, error)

        t0 = time.monotonic()
        observation_started_at = datetime.now(timezone.utc)
        tool.stats.total += 1
        try:
            # 参数校验（根据 JSON Schema 的 required 和 properties.type）
            try:
                self._validate_params(tool, params, context)
            except ValueError as exc:
                raise ToolRejected(str(exc)) from exc

            data = await asyncio.wait_for(self._run_handler(tool, params, context), timeout=tool.timeout_s)
            observed_at = datetime.now(timezone.utc)
            latency = (time.monotonic() - t0) * 1000

            effect_status = ToolEffectStatus.NONE if tool.read_only else ToolEffectStatus.OUTCOME_UNKNOWN
            receipt_id = ""
            if isinstance(data, ToolEffectReceipt):
                effect_status = data.effect_status
                receipt_id = str(data.receipt_id or "")
                data = data.data
            causal_source_call_ids: tuple[str, ...] = ()
            if isinstance(data, ToolObservationEnvelope):
                if not tool.read_only:
                    raise ValueError("observation provenance requires a read-only tool")
                causal_source_call_ids = tuple(dict.fromkeys(
                    str(call_id) for call_id in data.source_call_ids if str(call_id)
                ))
                data = data.data

            tool.stats.success += 1
            tool.stats.consecutive_fails = 0
            tool.stats.total_latency_ms += latency
            tool.breaker.record_success()

            # 重排（针对返回列表的检索工具）
            reranked = False
            if rerank_top_k > 0 and tool.supports_rerank and isinstance(data, list):
                query = params.get("query", "")
                data, reranked = await self._rerank(query, data, rerank_top_k), True

            # 写缓存：缓存最终返回结果，避免下次命中未重排的原始结果。
            if tool.cache_ttl > 0:
                self._set_cache(
                    name, params, data, tool.cache_ttl,
                    cache_rerank_top_k, reranked, cache_scope,
                    observation_started_at, observed_at,
                )

            return ToolResult(
                success=True,
                data=data,
                tool_name=name,
                latency_ms=latency,
                reranked=reranked,
                effect_status=effect_status.value,
                receipt_id=receipt_id,
                observation_started_at=observation_started_at,
                observed_at=observed_at,
                causal_source_call_ids=causal_source_call_ids,
            )

        except ToolRejected as exc:
            tool.stats.failed += 1
            return ToolResult(False, exc.data, name, error=str(exc),
                status=ToolCallStatus.REJECTED.value,
                effect_status=(ToolEffectStatus.NONE.value if tool.read_only
                               else ToolEffectStatus.NOT_COMMITTED.value))

        except asyncio.TimeoutError:
            tool.stats.failed += 1
            tool.stats.consecutive_fails += 1
            tool.breaker.record_failure()
            logger.error(f"工具超时: {name} ({tool.timeout_s}s)")
            result = await self._fallback_result(tool, params, context, "执行超时")
            result.status = ToolCallStatus.TIMEOUT.value
            result.effect_status = (
                ToolEffectStatus.NONE.value
                if tool.read_only
                else ToolEffectStatus.OUTCOME_UNKNOWN.value
            )
            return result

        except Exception as ex:
            tool.stats.failed += 1
            tool.stats.consecutive_fails += 1
            tool.breaker.record_failure()
            logger.error(f"工具异常: {name} — {ex}")
            return await self._fallback_result(tool, params, context, str(ex))

    async def _fallback_result(
        self,
        tool: Tool,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
        error: str,
    ) -> ToolResult:
        """工具不可用时返回降级结果，而不是把空错误直接暴露给调用方。"""
        if tool.fallback is None:
            return ToolResult(success=False, data=None, tool_name=tool.name, error=error)
        try:
            data = tool.fallback(params, context, error)
            if asyncio.iscoroutine(data):
                data = await data
            return ToolResult(
                success=True,
                data=data,
                tool_name=tool.name,
                error=error,
            )
        except Exception as ex:
            logger.error(f"工具降级失败: {tool.name} — {ex}")
            return ToolResult(success=False, data=None, tool_name=tool.name, error=f"{error}; fallback失败: {ex}")

    async def _run_handler(
        self,
        tool: Tool,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Any:
        """
        执行工具 handler。

        优先支持 async handler；如果历史工具仍是同步函数，则放入线程池执行，
        避免阻塞事件循环。
        """
        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(params, context)
        result = await asyncio.to_thread(tool.handler, params, context)
        if inspect.isawaitable(result):
            return await result
        return result

    # ── 查询改写（解决召回不全）────────────────────────────────────────────────

    async def rewrite_query(self, query: str, n: int = 3) -> List[str]:
        """
        用 LLM 将原始查询改写为 n 个不同角度的子查询。

        目的：单一查询往往只能召回某一角度的文档，
        多角度子查询并行检索后合并，显著提升召回率。

        示例：
          原始: "退款流程"
          改写: ["如何申请退款", "退款需要多少天", "退款政策是什么"]
        """
        expansions, _error = await self._query_transformer.expand(query, n=n)
        return list(dict.fromkeys([query, *expansions]))

    # ── 结果重排（解决召回不好）──────────────────────────────────────────────

    async def _rerank(self, query: str, items: List[Any], top_k: int) -> List[Any]:
        """
        用 LLM 对召回结果重新打分排序。

        解决问题：向量检索的相似度分数不等于"对用户有用"，
        LLM 能理解语义相关性，重排后 Top-K 质量显著提升。
        """
        reranked, _result = await self._rerank_detailed(query, items, top_k)
        return reranked

    async def _rerank_detailed(
        self, query: str, items: List[Any], top_k: int,
    ) -> tuple[List[Any], RerankResult]:
        """返回结果及严格排列合同状态，供一次检索快照审计。"""
        candidates = candidates_from_items(items)
        if len(items) <= top_k:
            ids = tuple(candidate.candidate_id for candidate in candidates)
            return items, RerankResult(ids, ids)

        result = await self._result_reranker.rerank(query, candidates)
        by_id = {candidate.candidate_id: item for candidate, item in zip(candidates, items)}
        return [by_id[candidate_id] for candidate_id in result.ordered_ids[:top_k]], result

    # ── 缓存 ──────────────────────────────────────────────────────────────────

    def _cache_key(
        self, name: str, params: Dict, rerank_top_k: int = 0, cache_scope: str = "",
    ) -> str:
        """对工具名、参数和重排配置计算稳定缓存键。"""
        payload = {
            "params": params,
            "rerank_top_k": rerank_top_k,
            "cache_scope": str(cache_scope),
        }
        return f"{name}:{hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()}"

    def _get_cache(
        self, name: str, params: Dict, rerank_top_k: int = 0, cache_scope: str = "",
    ) -> Optional[Tuple[Any, bool, datetime | None, datetime | None]]:
        """读取未过期缓存；过期项会在读取时删除。"""
        key = self._cache_key(name, params, rerank_top_k, cache_scope)
        if key in self._cache:
            data, expire_at, reranked, started_at, observed_at = self._cache[key]
            if time.monotonic() < expire_at:
                return data, reranked, started_at, observed_at
            del self._cache[key]
        return None

    def _set_cache(
        self,
        name: str,
        params: Dict,
        data: Any,
        ttl: float,
        rerank_top_k: int = 0,
        reranked: bool = False,
        cache_scope: str = "",
        observation_started_at: datetime | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        """写入带单调时钟过期点的进程内缓存。"""
        if len(self._cache) >= 5000:
            # 清掉最旧的 1/4
            for k in list(self._cache)[:1250]:
                del self._cache[k]
        self._cache[self._cache_key(name, params, rerank_top_k, cache_scope)] = (
            data, time.monotonic() + ttl, reranked, observation_started_at, observed_at,
        )

    # ── Agent 授权、审批与审计 ─────────────────────────────────────────────────

    def _requires_approval(self, tool: Tool) -> bool:
        """由宿主策略和工具静态风险共同决定是否需要批准。"""
        if self._approval_mode is ApprovalMode.AUTO_APPROVE:
            return False
        if self._approval_mode is ApprovalMode.REQUIRE_ALL:
            return True
        return tool.requires_approval or tool.risk is ToolRisk.HIGH or not tool.read_only

    def _finish_controlled_call(
        self,
        *,
        result: ToolResult,
        tool: Optional[Tool],
        agent_type: str,
        params: Dict[str, Any],
        context: Dict[str, Any],
        trace_id: str,
        call_id: str,
        request_id: str,
        started_iso: str,
        started: float,
        status: ToolCallStatus,
        approved: bool,
    ) -> ToolResult:
        """在唯一出口闭合结果字段并追加脱敏审计。"""
        result.call_id = call_id
        result.trace_id = trace_id
        result.status = status.value
        query_identity = json.dumps({
            "tool": result.tool_name, "params": params,
            "tenant_id": context.get("tenant_id"), "user_id": context.get("user_id"),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        result.query_ref = "tool-query:v1:" + hashlib.sha256(query_identity.encode("utf-8")).hexdigest()
        if tool is not None:
            result.authority = tool.authority
            result.output_schema_version = tool.output_schema_version
            result.receipt_schema_version = tool.receipt_schema_version
            if tool.read_only and result.success and not result.causal_source_call_ids:
                result.causal_source_call_ids = (call_id,)
        result.output_for_model = self._render_for_model(result)
        output_decision = UntrustedContentGuard().analyze(result.output_for_model)
        if output_decision.blocked:
            result.success = False
            result.data = None
            result.error = "untrusted tool output quarantined"
            status = ToolCallStatus.UNTRUSTED_OUTPUT
            result.status = status.value
            result.output_for_model = json.dumps({
                "status": status.value,
                "success": False,
                "error": "tool output was quarantined by input security policy",
                "effect_status": result.effect_status,
                "receipt_id": result.receipt_id,
            }, sort_keys=True)
        risk = tool.risk if tool else ToolRisk.HIGH
        read_only = tool.read_only if tool else True
        payload = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
        record = ToolAuditRecord(
            trace_id=trace_id,
            call_id=call_id,
            request_id=request_id,
            agent_type=agent_type,
            tool_name=result.tool_name,
            status=status,
            risk=risk,
            read_only=read_only,
            params_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
            params_summary=self._params_summary(params),
            result_summary=self._result_summary(result),
            approved=approved,
            started_at=started_iso,
            latency_ms=(time.monotonic() - started) * 1000,
            error="tool execution failed" if result.error else "",
            effect_status=ToolEffectStatus(result.effect_status),
            receipt_id=result.receipt_id,
            invocation_key=str(context.get("invocation_key") or ""),
            operation_key=str(context.get("operation_key") or ""),
        )
        self._audit.append(record)
        return result

    def _render_for_model(self, result: ToolResult) -> str:
        """格式化完整工具终态；模型视图预算由 Agent 上下文边界负责。"""
        if result.authority == "knowledge.active_source":
            from application.knowledge_tool_contract import model_evidence
            return json.dumps(model_evidence(result.data), ensure_ascii=False, separators=(",", ":"))
        payload = json.dumps(
            {
                "status": result.status,
                "success": result.success,
                "data": result.data,
                "error": result.error,
                "effect_status": result.effect_status,
                "receipt_id": result.receipt_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        # Keep the result intact. The Agent's context boundary persists and
        # projects oversized results with a readable reference.
        return payload

    @staticmethod
    def _params_summary(params: Dict[str, Any]) -> str:
        """只记录参数名、类型和规模，不把密码/正文写入审计。"""
        parts = []
        for key, value in list(params.items())[:24]:
            if isinstance(value, str):
                shape = f"str[{len(value)}]"
            elif isinstance(value, (list, tuple, set, dict)):
                shape = f"{type(value).__name__}[{len(value)}]"
            else:
                shape = type(value).__name__
            parts.append(f"{str(key)[:60]}:{shape}")
        return ", ".join(parts)[:500]

    @staticmethod
    def _result_summary(result: ToolResult) -> str:
        """审计只记录结果形状；完整输出只存在当前执行上下文。"""
        data = result.data
        size = len(data) if isinstance(data, (str, list, tuple, set, dict)) else 0
        return (
            f"status={result.status}; success={result.success}; "
            f"data={type(data).__name__}[{size}]; output_chars={len(result.output_for_model)}"
        )[:240]

    # ── 参数校验 ──────────────────────────────────────────────────────────────

    def _validate_params(self, tool: Tool, params: Dict[str, Any], context=None) -> None:
        """根据工具的 JSON Schema 校验参数，不合法时抛出 ValueError。"""
        schema = tool.input_schema(context)
        import jsonschema
        try:
            jsonschema.validate(params, schema)
        except jsonschema.ValidationError as exc:
            raise ValueError("parameters do not match tool schema") from exc

    @staticmethod
    def _strip_control_params(params: Dict[str, Any]) -> Dict[str, Any]:
        """移除只属于宿主控制面的字段，禁止模型参数污染审批状态。"""
        return {
            key: value
            for key, value in dict(params or {}).items()
            if key not in {"approved", "approval_token"}
        }

    @staticmethod
    def _clean_text(value: Any) -> str:
        """移除 Unicode 代理字符，避免 LLM 请求编码失败。"""
        if value is None:
            return ""
        if not isinstance(value, str):
            value = str(value)
        return value.encode("utf-8", errors="ignore").decode("utf-8")

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """返回工具统计和熔断状态的只读 API 投影。"""
        return {
            name: {
                "total": t.stats.total,
                "success_rate": round(t.stats.success_rate, 3),
                "avg_latency_ms": round(t.stats.avg_latency_ms, 1),
                "consecutive_fails": t.stats.consecutive_fails,
                "circuit_state": t.breaker.state.value,
            }
            for name, t in self._tools.items()
        }
