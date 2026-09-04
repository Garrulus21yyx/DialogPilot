import asyncio

from agents.agent_orchestrator import AgentResponse
from agents.orchestration_contracts import AgentType
from application.agent_result import AgentResult, AgentResultStatus
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.orchestration_runtime import AgentContextView
from application.work_item import ArgumentValue, ControlMode, WorkItem
from infrastructure.target_agent_execution import TargetAgentExecutor
from mcp.tool_manager import ToolCallStatus, ToolResult


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
    executor = TargetAgentExecutor({AgentType.GENERAL: agent})

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
    executor = TargetAgentExecutor({AgentType.GENERAL: agent})

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
        skill_executors={"product_identification": skill},
    )

    result = asyncio.run(executor(_context(_item(
        skill_hint="product_identification", requirements=(),
    ))))

    assert result.status is AgentResultStatus.SUCCEEDED
    assert agent.requests == []
