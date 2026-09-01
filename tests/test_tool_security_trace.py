"""Agent 工具授权、审批、输出边界和 Trace 审计的不变量。"""

import asyncio

import pytest

from core.identity import InvocationKey, TenantId, UserId, ConversationId, RequestId
from core.tracing import TraceRecorder, current_trace_id, trace_scope
from mcp.tool_manager import (
    ApprovalMode,
    MCPToolManager,
    Tool,
    ToolCallStatus,
    ToolEffectStatus,
    ToolRisk,
)


def manager(**kwargs):
    return MCPToolManager(api_key="test-key", model="test-model", **kwargs)


def test_tool_discovery_and_execution_share_agent_allowlist():
    """证明隐藏工具和执行权限读取同一 allowlist，不能只在 Prompt 层过滤。"""
    runtime = manager()
    called = []

    async def handler(params, _context):
        called.append(params)
        return {"ok": True}

    runtime.register(Tool(
        name="security_lookup",
        description="只供账户安全 Agent 查询",
        handler=handler,
        schema={"type": "object", "properties": {}},
        allowed_agents=("account_security",),
    ))

    assert runtime.anthropic_tools_for_agent("billing") == []
    result = asyncio.run(runtime.execute_for_agent(
        "security_lookup", {}, agent_type="billing", context={"request_id": "r1"}
    ))

    assert result.status == ToolCallStatus.DENIED.value
    assert called == []
    assert runtime.audit_records()[0].status is ToolCallStatus.DENIED


def test_tool_execution_derives_operation_key_from_application_invocation():
    runtime = manager()
    observed_context = {}

    async def handler(_params, context):
        observed_context.update(context)
        return {"ok": True}

    runtime.register(Tool(
        name="identity_lookup",
        description="identity propagation test",
        handler=handler,
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    invocation_key = InvocationKey.build(
        TenantId("tenant-1"), UserId("user-1"),
        ConversationId("conversation-1"), RequestId("request-1"),
    )
    asyncio.run(runtime.execute_for_agent(
        "identity_lookup",
        {},
        agent_type="general",
        call_id="call-1",
        context={
            "request_id": "request-1",
            "invocation_key": str(invocation_key),
        },
    ))
    audit = runtime.audit_records()[0]
    assert audit.invocation_key == invocation_key
    assert audit.operation_key.startswith("operation:v1:")
    assert observed_context["operation_key"] == audit.operation_key


def test_high_risk_tool_requires_host_approval_before_side_effect():
    """证明模型提出高风险调用不会执行，只有宿主侧 approved 才能放行。"""
    runtime = manager()
    effects = []

    async def handler(params, _context):
        effects.append(params["account_id"])
        return {"receipt": "approved-operation"}

    runtime.register(Tool(
        name="freeze_account",
        description="冻结账户",
        handler=handler,
        schema={
            "type": "object",
            "properties": {"account_id": {"type": "string"}},
            "required": ["account_id"],
        },
        allowed_agents=("account_security",),
        risk=ToolRisk.HIGH,
        read_only=False,
    ))

    pending = asyncio.run(runtime.execute_for_agent(
        "freeze_account",
        {"account_id": "A-1"},
        agent_type="account_security",
    ))
    approved = asyncio.run(runtime.execute_for_agent(
        "freeze_account",
        {"account_id": "A-1"},
        agent_type="account_security",
        approved=True,
    ))

    assert pending.status == ToolCallStatus.AWAITING_APPROVAL.value
    assert effects == ["A-1"]
    assert approved.status == ToolCallStatus.SUCCESS.value
    assert [record.status for record in runtime.audit_records()] == [
        ToolCallStatus.AWAITING_APPROVAL,
        ToolCallStatus.SUCCESS,
    ]


def test_trace_context_propagates_to_parallel_tool_calls_and_redacts_audit():
    """证明 asyncio 子任务继承 TraceId，审计不保存敏感参数或完整结果。"""
    recorder = TraceRecorder()
    runtime = manager(trace_recorder=recorder)

    async def handler(params, _context):
        await asyncio.sleep(0)
        return {"echo": params["password"]}

    runtime.register(Tool(
        name="read_profile",
        description="读取画像",
        handler=handler,
        schema={
            "type": "object",
            "properties": {"password": {"type": "string"}},
            "required": ["password"],
        },
        allowed_agents=("general",),
    ))

    async def run_calls():
        with trace_scope("trace-123"):
            assert current_trace_id() == "trace-123"
            return await asyncio.gather(*[
                runtime.execute_for_agent(
                    "read_profile",
                    {"password": f"secret-{index}"},
                    agent_type="general",
                    context={"request_id": "r2"},
                )
                for index in range(2)
            ])

    results = asyncio.run(run_calls())
    records = runtime.audit_records(trace_id="trace-123")
    spans = recorder.get_trace("trace-123")

    assert all(result.trace_id == "trace-123" for result in results)
    assert len(records) == len(spans) == 2
    assert all(record.params_hash and "secret" not in record.params_summary for record in records)
    assert all("secret" not in record.result_summary for record in records)
    assert all(span.kind == "tool" and span.status == "ok" for span in spans)


def test_tool_output_is_bounded_before_react_context_writeback():
    """证明超长工具结果在回写模型前保留头尾并受字符预算约束。"""
    runtime = manager(max_output_chars=300, approval_mode=ApprovalMode.AUTO_APPROVE)

    async def handler(_params, _context):
        return "HEAD-" + "x" * 1000 + "-TAIL"

    runtime.register(Tool(
        name="long_read",
        description="返回长结果",
        handler=handler,
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    result = asyncio.run(runtime.execute_for_agent("long_read", {}, agent_type="general"))

    assert len(result.output_for_model) <= 320
    assert "HEAD-" in result.output_for_model
    assert "-TAIL" in result.output_for_model
    assert "truncated" in result.output_for_model


def test_write_timeout_reports_unknown_effect_even_when_child_commits_late():
    """Timeout 是调用终态，不冒充下游事务的零副作用证明。"""
    runtime = manager(approval_mode=ApprovalMode.AUTO_APPROVE)
    effects = []

    async def handler(_params, _context):
        async def commit_later():
            await asyncio.sleep(0.02)
            effects.append("late-commit")

        asyncio.create_task(commit_later())
        await asyncio.sleep(0.2)

    runtime.register(Tool(
        name="late_write",
        description="迟到写入",
        handler=handler,
        schema={"type": "object", "properties": {}},
        timeout_s=0.001,
        read_only=False,
        allowed_agents=("billing",),
    ))

    async def run():
        result = await runtime.execute_for_agent("late_write", {}, agent_type="billing")
        await asyncio.sleep(0.04)
        return result

    result = asyncio.run(run())
    audit = runtime.audit_records()[0]
    assert effects == ["late-commit"]
    assert result.status == ToolCallStatus.TIMEOUT.value
    assert result.effect_status == ToolEffectStatus.OUTCOME_UNKNOWN.value
    assert audit.status is ToolCallStatus.TIMEOUT
    assert audit.effect_status is ToolEffectStatus.OUTCOME_UNKNOWN


def test_external_cancellation_has_one_terminal_correlated_audit():
    runtime = manager(approval_mode=ApprovalMode.AUTO_APPROVE)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def handler(_params, _context):
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    runtime.register(Tool(
        name="cancel_write",
        description="取消写入",
        handler=handler,
        schema={"type": "object", "properties": {}},
        read_only=False,
        allowed_agents=("billing",),
    ))

    async def run():
        task = asyncio.create_task(runtime.execute_for_agent(
            "cancel_write",
            {},
            agent_type="billing",
            call_id="call-cancel-1",
            context={"trace_id": "trace-cancel-1"},
        ))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()

    asyncio.run(run())
    records = runtime.audit_records(trace_id="trace-cancel-1")
    assert len(records) == 1
    assert records[0].call_id == "call-cancel-1"
    assert records[0].status is ToolCallStatus.CANCELLED
    assert records[0].effect_status is ToolEffectStatus.OUTCOME_UNKNOWN


def test_model_approval_parameters_never_reach_write_handler():
    runtime = manager(approval_mode=ApprovalMode.AUTO_APPROVE)
    received = []

    async def handler(params, _context):
        received.append(params)
        return {"ok": True}

    runtime.register(Tool(
        name="controlled_write",
        description="控制面参数隔离",
        handler=handler,
        schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "approved": {"type": "boolean"},
                "approval_token": {"type": "string"},
            },
        },
        read_only=False,
        allowed_agents=("billing",),
    ))

    asyncio.run(runtime.execute_for_agent(
        "controlled_write",
        {"order_id": "A-1", "approved": True, "approval_token": "forged"},
        agent_type="billing",
    ))
    assert received == [{"order_id": "A-1"}]
