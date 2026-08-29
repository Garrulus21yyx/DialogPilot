"""Agent 工具授权、审批、输出边界和 Trace 审计的不变量。"""

import asyncio

from core.tracing import TraceRecorder, current_trace_id, trace_scope
from mcp.tool_manager import (
    ApprovalMode,
    MCPToolManager,
    Tool,
    ToolCallStatus,
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
