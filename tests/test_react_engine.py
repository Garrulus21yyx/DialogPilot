"""有界 ReAct 循环、工具结果配对和失败终态测试。"""

import asyncio
from types import SimpleNamespace

from agents.react_engine import ReActExecutionEngine, ReActStatus
from core.tracing import TraceRecorder, trace_scope
from mcp.tool_manager import MCPToolManager, Tool, ToolRisk


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(call_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


class ScriptedClient:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=self.contents.pop(0))


def runtime(recorder=None):
    return MCPToolManager(
        api_key="test-key",
        model="test-model",
        trace_recorder=recorder,
    )


def engine(client, tools, *, max_steps=4, recorder=None):
    return ReActExecutionEngine(
        client=client,
        model="test-model",
        tool_manager=tools,
        trace_recorder=recorder,
        max_steps=max_steps,
    )


def test_react_executes_read_tool_and_pairs_result_before_final_answer():
    """证明 tool_use 与 tool_result 按 call_id 配对，并在下一轮生成终答。"""
    recorder = TraceRecorder()
    tools = runtime(recorder)

    async def lookup(params, _context):
        return {"order_id": params["order_id"], "status": "refunding"}

    tools.register(Tool(
        name="order_lookup",
        description="查询订单",
        handler=lookup,
        schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        allowed_agents=("billing",),
    ))
    client = ScriptedClient([
        [text("我先核实订单。"), tool_use("call-1", "order_lookup", {"order_id": "A123"})],
        [text("订单 A123 正在退款处理中。")],
    ])

    async def run():
        with trace_scope("trace-react"):
            return await engine(client, tools, recorder=recorder).run(
                system="billing worker",
                messages=[{"role": "user", "content": "查 A123"}],
                agent_type="billing",
                execution_context={"request_id": "r1"},
            )

    result = asyncio.run(run())

    assert result.status is ReActStatus.COMPLETED
    assert result.steps == 2
    assert result.tool_call_ids == ("call-1",)
    tool_results = client.calls[1]["messages"][-1]["content"]
    assert tool_results[0]["tool_use_id"] == "call-1"
    assert tool_results[0]["is_error"] is False
    assert len(recorder.get_trace("trace-react")) == 3  # 两个 LLM step + 一个 tool


def test_react_high_risk_call_closes_as_blocked_without_side_effect():
    """证明待审批工具即使模型随后给出文本，任务仍不能标记完成。"""
    tools = runtime()
    effects = []

    async def refund(params, _context):
        effects.append(params)
        return {"receipt": "should-not-exist"}

    tools.register(Tool(
        name="issue_refund",
        description="执行退款",
        handler=refund,
        schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        allowed_agents=("billing",),
        risk=ToolRisk.HIGH,
        read_only=False,
    ))
    client = ScriptedClient([
        [tool_use("call-risk", "issue_refund", {"order_id": "A123"})],
        [text("退款需要人工审批。")],
    ])

    result = asyncio.run(engine(client, tools).run(
        system="billing worker",
        messages=[{"role": "user", "content": "退款"}],
        agent_type="billing",
    ))

    assert result.status is ReActStatus.BLOCKED
    assert result.success is False
    assert effects == []
    assert client.calls[1]["messages"][-1]["content"][0]["is_error"] is True


def test_react_stops_repeated_tool_loop_at_max_steps():
    """证明模型持续调用工具时由 max_steps 确定性停止，而不是无限循环。"""
    tools = runtime()

    async def lookup(_params, _context):
        return {"ok": True}

    tools.register(Tool(
        name="lookup",
        description="查询",
        handler=lookup,
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    client = ScriptedClient([
        [tool_use("c1", "lookup", {})],
        [tool_use("c2", "lookup", {})],
    ])

    result = asyncio.run(engine(client, tools, max_steps=2).run(
        system="general worker",
        messages=[{"role": "user", "content": "继续查"}],
        agent_type="general",
    ))

    assert result.status is ReActStatus.MAX_STEPS
    assert result.steps == 2
    assert result.tool_call_ids == ("c1", "c2")
    assert "最大步数" in result.content


def test_react_unauthorized_tool_name_is_denied_even_if_model_invents_it():
    """证明模型可编造工具名，但执行边界仍以注册表和 allowlist 为准。"""
    tools = runtime()
    tools.register(Tool(
        name="billing_only",
        description="账务专用",
        handler=lambda _params, _context: {"ok": True},
        schema={"type": "object", "properties": {}},
        allowed_agents=("billing",),
    ))
    tools.register(Tool(
        name="general_read",
        description="通用查询",
        handler=lambda _params, _context: {"ok": True},
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    client = ScriptedClient([
        [tool_use("invented", "billing_only", {})],
        [text("没有权限查询账务工具。")],
    ])

    result = asyncio.run(engine(client, tools).run(
        system="general worker",
        messages=[{"role": "user", "content": "越权调用"}],
        agent_type="general",
    ))

    assert result.status is ReActStatus.BLOCKED
    assert tools.audit_records()[0].status.value == "denied"
