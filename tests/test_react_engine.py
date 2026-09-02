"""有界 ReAct 循环、工具结果配对和失败终态测试。"""

import asyncio
import json
from types import SimpleNamespace

from agents.react_engine import ReActExecutionEngine, ReActStatus
from agents.tool_result_context import render_tool_result_context
from core.provider_context_budget import (
    ProviderContextBudget,
    ProviderContextBudgetExceeded,
)
from core.tracing import TraceRecorder, trace_scope
from core.model_policy import ModelProfile, ModelRole, ReasoningEffort
from mcp.tool_manager import MCPToolManager, Tool, ToolResult, ToolRisk


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool_use(call_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


def thinking(value, signature="sig-1"):
    return SimpleNamespace(type="thinking", thinking=value, signature=signature)


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
        authority="CustomerOperations",
        output_schema_version="order-view-v1",
        receipt_schema_version="evidence-receipt-v1",
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
    assert [item.to_dict() for item in result.tool_receipts] == [{
        "call_id": "call-1",
        "tool_name": "order_lookup",
        "status": "success",
        "authority": "CustomerOperations",
        "output_schema_version": "order-view-v1",
        "receipt_schema_version": "evidence-receipt-v1",
        "effect_status": "none",
        "receipt_id": "",
    }]
    tool_results = client.calls[1]["messages"][-1]["content"]
    assert tool_results[0]["tool_use_id"] == "call-1"
    assert tool_results[0]["is_error"] is False
    projected = json.loads(tool_results[0]["content"])
    assert projected["schema_version"] == "tool-result-context-v1"
    assert projected["result_locator"] == ""
    assert projected["authority"] == "CustomerOperations"
    assert "refunding" in projected["result_excerpt"]
    spans = recorder.get_trace("trace-react")
    assert len(spans) == 5  # 两个 Agent step、两个 generation、一个 tool
    assert [span.kind for span in spans].count("agent") == 2
    assert [span.kind for span in spans].count("llm") == 2
    assert [span.kind for span in spans].count("tool") == 1


def test_react_high_risk_call_pauses_before_second_model_step_without_side_effect():
    """待审批是可恢复状态；不伪造 tool_result，也不继续第二轮模型。"""
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

    assert result.status is ReActStatus.WAITING_APPROVAL
    assert result.success is False
    assert effects == []
    assert result.pending_approval_call_ids == ("call-risk",)
    assert len(client.calls) == 1


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
    assert [item.call_id for item in result.tool_receipts] == ["c1", "c2"]
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


def test_react_returns_thinking_block_before_tool_result_turn():
    """证明开启 reasoning 时，不会在工具第二轮丢失供应商要求的思考块。"""
    tools = runtime()
    tools.register(Tool(
        name="lookup",
        description="查询",
        handler=lambda _params, _context: {"ok": True},
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    client = ScriptedClient([
        [thinking("need a lookup"), tool_use("c1", "lookup", {})],
        [text("查询完成。")],
    ])
    profile = ModelProfile("deepseek-v4-pro", ReasoningEffort.HIGH, "deepseek", 1024)
    react = ReActExecutionEngine(
        client=client,
        model=profile.model,
        model_profile=profile,
        tool_manager=tools,
    )

    result = asyncio.run(react.run(
        system="worker",
        messages=[{"role": "user", "content": "查一下"}],
        agent_type="general",
    ))

    assert result.status is ReActStatus.COMPLETED
    assert client.calls[0]["extra_body"]["thinking"]["type"] == "enabled"
    assistant_blocks = client.calls[1]["messages"][-2]["content"]
    assert assistant_blocks[0] == {
        "type": "thinking", "thinking": "need a lookup", "signature": "sig-1",
    }
    assert assistant_blocks[1]["type"] == "tool_use"


def test_react_compacts_latest_tool_excerpt_before_single_provider_attempt():
    tools = runtime()
    tools.register(Tool(
        name="lookup", description="lookup", handler=lambda *_args: {},
        schema={"type": "object", "properties": {}},
        allowed_agents=("general",),
    ))
    result = ToolResult(
        True, {}, "lookup", call_id="old-call", status="success",
        output_for_model="large result " * 1_000,
    )
    messages = [{
        "role": "user",
        "content": [{
            "type": "tool_result", "tool_use_id": "old-call",
            "content": render_tool_result_context(result, result_locator="locator"),
            "is_error": False,
        }],
    }]
    profile = ModelProfile("test-model", max_context_tokens=1024)
    tool_schemas = tools.anthropic_tools_for_agent("general")
    budget = ProviderContextBudget()
    compacted = [json.loads(json.dumps(messages[0]))]
    payload = json.loads(compacted[0]["content"][0]["content"])
    payload["result_excerpt"] = ""
    payload["compacted"] = True
    compacted[0]["content"][0]["content"] = json.dumps(payload, sort_keys=True)
    system = None
    for size in range(1_000, 5_000, 10):
        candidate = "s" * size
        request = {
            "max_tokens": 64, "system": candidate,
            "messages": messages, "tools": tool_schemas,
        }
        compact_request = {**request, "messages": compacted}
        try:
            budget.validate(profile, ModelRole.REACT, request)
        except ProviderContextBudgetExceeded:
            if budget.validate(
                profile, ModelRole.REACT, compact_request,
            ).total_reserved_tokens <= 1024:
                system = candidate
                break
    assert system is not None
    client = ScriptedClient([[text("done")]])
    react = ReActExecutionEngine(
        client=client, model=profile.model, model_profile=profile,
        tool_manager=tools, max_tokens=64,
    )
    outcome = asyncio.run(react.run(
        system=system, messages=messages, agent_type="general",
    ))
    assert outcome.status is ReActStatus.COMPLETED
    assert len(client.calls) == 1
    sent = json.loads(client.calls[0]["messages"][0]["content"][0]["content"])
    assert sent["result_excerpt"] == ""
    assert sent["result_locator"] == "locator"
