import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.context_budget import ContextBudgetManager
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import AgentContextView
from application.work_item import ArgumentValue, ControlMode, WorkItem
from infrastructure.target_framework_agent import TargetFrameworkAgent, _thread_id
from mcp.tool_manager import MCPToolManager, Tool


class ScriptedToolModel(BaseChatModel):
    responses: list[AIMessage]
    calls: int = 0
    bound_tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools: Sequence[Any], **kwargs):
        self.bound_tool_names = [tool.name for tool in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        response = self.responses[self.calls]
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=response)])


def _manager(calls):
    manager = MCPToolManager("test-key", model="test-model")

    async def catalog(params, context):
        calls.append((params, context))
        return {"canonical_model": "PX-200"}

    manager.register(Tool(
        "catalog_search",
        "Search the general product catalog",
        catalog,
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        allowed_agents=("technical",),
        authority="product.canonical_model",
        output_schema_version="catalog-result-v1",
    ))
    return manager


def _item(
    *,
    control_mode=ControlMode.DELEGATED,
    allowed_tools=("catalog_search",),
    skill_hint=None,
):
    return WorkItem(
        "product-work-1",
        "product_technical",
        "Resolve the supplied product question",
        control_mode,
        allowed_tools,
        ("product_identification",) if skill_hint else (),
        (ArgumentValue.create("question", "这是什么型号？"),),
        ("product.canonical_model",),
        (),
        CapabilityEffect.READ,
        CapabilityRisk.MEDIUM,
        "agent-result-v1",
        "customer-service-default:v1",
        1,
        "registry:test",
        8,
        4,
        skill_hint=skill_hint,
    )


def _context(item=None, **trusted):
    return AgentContextView(
        item or _item(),
        "请帮我确认这个商品的型号",
        (),
        ("用户正在询问当前商品。",),
        (),
        2000,
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "conversation_id": "conversation-a",
            "request_id": "request-a",
            "invocation_key": "invocation-a",
            **trusted,
        },
    )


def test_framework_agent_uses_only_governed_tools_and_returns_provenance():
    calls = []
    manager = _manager(calls)
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "catalog_search",
            "args": {"query": "当前商品"},
            "id": "tool-call-1",
            "type": "tool_call",
        }]),
        AIMessage(content="目录确认型号为 PX-200。"),
    ])
    agent = TargetFrameworkAgent(
        model,
        manager,
        registry=build_default_capability_registry("tenant-a"),
        system_prompt="You are a general ecommerce product specialist.",
    )

    result = asyncio.run(agent(_context()))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.candidate_response == "目录确认型号为 PX-200。"
    assert result.facts[0].requirement_id == "product.canonical_model"
    assert result.facts[0].source_ref
    assert model.bound_tool_names == ["catalog_search"]
    assert calls[0][0] == {"query": "当前商品"}
    assert calls[0][1]["agent_type"] == "technical"


def test_framework_agent_rejects_invalid_envelope_before_model_or_tool():
    calls = []
    model = ScriptedToolModel(responses=[AIMessage(content="不应调用")])
    agent = TargetFrameworkAgent(
        model,
        _manager(calls),
        registry=build_default_capability_registry("tenant-a"),
        system_prompt="product",
    )

    result = asyncio.run(agent(_context(_item(allowed_tools=("refund_status",)))))

    assert result.status is AgentResultStatus.TERMINAL_FAILURE
    assert result.reason_code == "INVALID_AGENT_CAPABILITY_ENVELOPE"
    assert model.calls == 0
    assert calls == []


def test_framework_agent_enforces_context_budget_before_model_or_tool():
    calls = []
    model = ScriptedToolModel(responses=[AIMessage(content="不应调用")])
    agent = TargetFrameworkAgent(
        model,
        _manager(calls),
        registry=build_default_capability_registry("tenant-a"),
        system_prompt="product",
        context_budget=ContextBudgetManager(
            context_window_tokens=300,
            reserved_output_tokens=100,
            protocol_reserve_tokens=100,
        ),
    )
    context = _context()
    context = replace(context, current_message="问题" * 1000)

    result = asyncio.run(agent(context))

    assert result.status is AgentResultStatus.TERMINAL_FAILURE
    assert result.reason_code == "CONTEXT_BUDGET_EXCEEDED"
    assert model.calls == 0
    assert calls == []


def test_pinned_skill_bypasses_framework_replanning():
    async def skill(context):
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "SKILL_COMPLETED",
            "skill-v1",
        )

    model = ScriptedToolModel(responses=[AIMessage(content="不应调用")])
    agent = TargetFrameworkAgent(
        model,
        _manager([]),
        registry=build_default_capability_registry("tenant-a"),
        system_prompt="product",
        skill_executors={"product_identification": skill},
    )

    result = asyncio.run(agent(_context(_item(skill_hint="product_identification"))))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert model.calls == 0


def test_open_goal_can_choose_optional_composite_skill():
    async def skill(context):
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "PRODUCT_IDENTIFIED",
            "skill-v1",
            facts=(FactRecord(
                "product:PX-200",
                "product.canonical_model",
                json.dumps({"canonical_model": "PX-200"}, separators=(",", ":")),
                FactSourceKind.VERIFIED_STATE,
                "catalog-receipt-1",
                "catalog_search",
                "catalog-v1",
                datetime.now(timezone.utc),
            ),),
            evidence_refs=("catalog-receipt-1",),
        )

    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "product_identification",
            "args": {"asset_id": "IMG9"},
            "id": "skill-call-1",
            "type": "tool_call",
        }]),
        AIMessage(content="已根据图片和目录确认型号。"),
    ])
    agent = TargetFrameworkAgent(
        model,
        _manager([]),
        registry=build_default_capability_registry("tenant-a"),
        system_prompt="product",
        skill_executors={"product_identification": skill},
    )
    item = replace(
        _item(),
        allowed_skills=("product_identification",),
        arguments=(ArgumentValue.create("asset_id", "IMG9"),),
    )

    result = asyncio.run(agent(_context(item)))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.evidence_refs == ("catalog-receipt-1",)
    assert set(model.bound_tool_names) == {
        "catalog_search", "product_identification",
    }


def test_framework_checkpoint_scope_is_workstream_or_invocation():
    first = _context(workstream_id="product-ws-1")
    second = _context(workstream_id="product-ws-2")
    one_off = _context()

    assert _thread_id(first) == "target-domain-workstream:product-ws-1"
    assert _thread_id(first) != _thread_id(second)
    assert _thread_id(one_off).endswith(":product-work-1")
