import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from agents.agent_orchestrator import AgentResponse, TechnicalAgent
from agents.orchestration_contracts import AgentType
from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    FactRecord,
    FactSourceKind,
)
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import AgentContextView
from application.work_item import ArgumentValue, ControlMode, WorkItem
from infrastructure.target_agent_execution import TargetAgentExecutor
from infrastructure.target_product_execution import TargetProductExecutor
from mcp.tool_manager import MCPToolManager, Tool, ToolCallStatus, ToolResult


class RecordingAgent:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def handle(self, request):
        self.requests.append(request)
        return self.response


def _item(*, skill_hint=None, requirements=("order.current_state",)):
    return WorkItem(
        "work-1",
        "order_logistics" if skill_hint is None else "product_technical",
        "Resolve the selected ecommerce task",
        ControlMode.DELEGATED,
        ("order_lookup",) if skill_hint is None else ("media_read", "catalog_search"),
        () if skill_hint is None else ("product_identification",),
        (ArgumentValue.create("order_id", "DP1234"),) if skill_hint is None else (
            ArgumentValue.create("asset_id", "IMG9"),
        ),
        requirements,
        (),
        CapabilityEffect.READ,
        CapabilityRisk.MEDIUM,
        "agent-result-v1",
        "customer-service-default:v1",
        1,
        "registry:v1:test",
        8,
        4,
        skill_hint=skill_hint,
    )


def _context(item):
    return AgentContextView(
        item,
        "查一下订单 DP1234",
        (),
        (),
        (),
        2000,
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "conversation_id": "conversation-a",
            "request_id": "request-a",
        },
    )


def test_delegated_work_reuses_existing_agent_with_work_item_envelope():
    tool_result = ToolResult(
        True,
        {"status": "shipped"},
        "order_lookup",
        call_id="call-1",
        status=ToolCallStatus.SUCCESS.value,
        authority="order.current_state",
        output_schema_version="order-state-v1",
    )
    agent = RecordingAgent(AgentResponse(
        AgentType.GENERAL,
        "订单已发货。",
        True,
        react_status="completed",
        tool_results=(tool_result,),
    ))
    executor = TargetAgentExecutor(
        {AgentType.GENERAL: agent},
        registry=build_default_capability_registry("tenant-a"),
    )

    result = asyncio.run(executor(_context(_item())))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.facts[0].requirement_id == "order.current_state"
    assert result.candidate_response == "订单已发货。"
    assert agent.requests[0].allowed_tool_ids == ("order_lookup",)
    assert agent.requests[0].assigned_task.objective == "Resolve the selected ecommerce task"


def test_delegated_agent_text_cannot_replace_missing_authoritative_fact():
    agent = RecordingAgent(AgentResponse(
        AgentType.GENERAL,
        "我猜订单已发货。",
        True,
        react_status="completed",
    ))
    executor = TargetAgentExecutor(
        {AgentType.GENERAL: agent},
        registry=build_default_capability_registry("tenant-a"),
    )

    result = asyncio.run(executor(_context(_item())))

    assert result.status is AgentResultStatus.TERMINAL_FAILURE
    assert result.facts == ()


def test_pinned_composite_skill_bypasses_agent_replanning():
    agent = RecordingAgent(AgentResponse(AgentType.TECHNICAL, "unused", True))

    async def skill(context):
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "SKILL_COMPLETED",
            "skill-v1",
        )

    executor = TargetAgentExecutor(
        {AgentType.TECHNICAL: agent},
        registry=build_default_capability_registry("tenant-a"),
        skill_executors={"product_identification": skill},
    )

    result = asyncio.run(executor(_context(_item(
        skill_hint="product_identification", requirements=(),
    ))))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert agent.requests == []


def test_unpinned_domain_agent_can_choose_registered_composite_skill():
    class SkillCallingAgent(RecordingAgent):
        async def handle(self, request):
            self.requests.append(request)
            capability = request.react_capabilities[0]
            tool_result = await capability.execute(
                {"asset_id": "IMG9"}, {}, "skill-call-1",
            )
            return AgentResponse(
                AgentType.TECHNICAL,
                "识别到 PX-200。",
                True,
                react_status="completed",
                tool_results=(tool_result,),
            )

    agent = SkillCallingAgent(None)

    async def skill(context):
        fact = FactRecord(
            "product:PX-200",
            "product.canonical_model",
            json.dumps({"canonical_model": "PX-200"}, separators=(",", ":")),
            FactSourceKind.VERIFIED_STATE,
            "catalog-receipt-1",
            "catalog_search",
            "catalog-v1",
            datetime.now(timezone.utc),
        )
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "PRODUCT_IDENTIFIED",
            "product-identification-v1",
            facts=(fact,),
            evidence_refs=("catalog-receipt-1",),
        )

    executor = TargetAgentExecutor(
        {AgentType.TECHNICAL: agent},
        registry=build_default_capability_registry("tenant-a"),
        skill_executors={"product_identification": skill},
    )
    item = WorkItem(
        "product-work-1",
        "product_technical",
        "Identify the product and answer its specification question",
        ControlMode.DELEGATED,
        ("media_read", "catalog_search", "knowledge_search"),
        ("product_identification",),
        (ArgumentValue.create("asset_id", "IMG9"),),
        ("product.canonical_model",),
        (),
        CapabilityEffect.READ,
        CapabilityRisk.MEDIUM,
        "agent-result-v1",
        "customer-service-default:v1",
        1,
        "registry:v1:test",
        8,
        4,
    )

    result = asyncio.run(executor(_context(item)))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert result.facts[0].requirement_id == "product.canonical_model"
    assert agent.requests[0].react_capabilities[0].name == "product_identification"


def test_existing_react_agent_combines_composite_skill_and_atomic_tool():
    class ScriptedClient:
        def __init__(self):
            self.messages = self
            self.responses = [
                [SimpleNamespace(
                    type="tool_use", id="skill-1", name="product_identification",
                    input={"asset_id": "IMG9"},
                )],
                [SimpleNamespace(
                    type="tool_use", id="knowledge-1", name="knowledge_search",
                    input={"query": "PX-200 是否支持 Mac"},
                )],
                [SimpleNamespace(type="text", text="PX-200 支持 Mac。")],
            ]

        async def create(self, **_kwargs):
            return SimpleNamespace(content=self.responses.pop(0))

    manager = MCPToolManager(api_key="test-key", model="test-model")

    async def media(_params, _context):
        return {"text": "PX-200", "evidence_ref": "media:IMG9"}

    async def catalog(_params, _context):
        return {
            "status": "MATCHED",
            "canonical_model": "PX-200",
            "product_name": "Dock",
            "source_ref": "catalog:PX-200",
        }

    async def knowledge(_params, _context):
        return {"answer": "PX-200 supports Mac", "source_ref": "kb:PX-200"}

    for name, handler, authority in (
        ("media_read", media, "media.visible_text"),
        ("catalog_search", catalog, "product.canonical_model"),
        ("knowledge_search", knowledge, "knowledge.active_source"),
    ):
        manager.register(Tool(
            name=name,
            description=name,
            handler=handler,
            schema={"type": "object", "properties": {}},
            allowed_agents=("technical",),
            authority=authority,
            output_schema_version=f"{name}-v1",
        ))
    agent = TechnicalAgent(
        ScriptedClient(), "test-model", tool_manager=manager, react_max_steps=4,
    )
    registry = build_default_capability_registry("tenant-a")
    executor = TargetAgentExecutor(
        {AgentType.TECHNICAL: agent},
        registry=registry,
        skill_executors={
            "product_identification": TargetProductExecutor(manager),
        },
    )
    item = WorkItem(
        "compound-product-1",
        "product_technical",
        "Identify the product and answer the related specification question",
        ControlMode.DELEGATED,
        ("media_read", "catalog_search", "knowledge_search"),
        ("product_identification",),
        (
            ArgumentValue.create("asset_id", "IMG9"),
            ArgumentValue.create("question", "是否支持 Mac"),
        ),
        ("product.canonical_model", "knowledge.active_source"),
        (),
        CapabilityEffect.READ,
        CapabilityRisk.MEDIUM,
        "agent-result-v1",
        "customer-service-default:v1",
        1,
        registry.fingerprint,
        8,
        4,
    )

    result = asyncio.run(executor(_context(item)))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert {fact.requirement_id for fact in result.facts} == {
        "product.canonical_model", "knowledge.active_source",
    }
    assert result.candidate_response == "PX-200 支持 Mac。"
