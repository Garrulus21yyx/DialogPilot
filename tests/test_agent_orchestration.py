import asyncio
import json
from types import SimpleNamespace

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentResponse,
    AgentType,
    Request,
    RoutingDecision,
)
from core.intent_recognizer import IntentCategory
from services.result_synthesizer import (
    AgentOutcome,
    AgentOutcomeStatus,
    ResultSynthesizer,
    SynthesisResult,
    SynthesisStatus,
)


def outcome(
    agent_type: str,
    status=AgentOutcomeStatus.SUCCESS,
    *,
    primary=False,
    content="answer",
):
    return AgentOutcome(
        agent_type=agent_type,
        responding_agent_type=agent_type,
        status=status,
        is_primary=primary,
        content=content if status is AgentOutcomeStatus.SUCCESS else "",
        error="failure" if status is not AgentOutcomeStatus.SUCCESS else "",
    )


def test_synthesizer_returns_partial_success_without_discarding_valid_answer():
    """证明部分失败时保留有效回答，同时返回 PARTIAL 并升级。"""
    synthesizer = ResultSynthesizer(client=None, model="test")
    result = asyncio.run(synthesizer.synthesize(
        "question",
        [
            outcome("technical", primary=True, content="technical answer"),
            outcome("billing", AgentOutcomeStatus.TIMEOUT),
        ],
    ))

    assert result.status is SynthesisStatus.PARTIAL
    assert result.content == "technical answer"
    assert result.successful_agents == ["technical"]
    assert result.failed_agents == ["billing"]
    assert result.escalate is True


def test_synthesizer_all_failed_is_typed_and_fail_closed():
    """证明全部 Agent 失败会收敛为 FAILED，而不是发布空或伪造内容。"""
    synthesizer = ResultSynthesizer(client=None, model="test")
    result = asyncio.run(synthesizer.synthesize(
        "question",
        [
            outcome("technical", AgentOutcomeStatus.ERROR, primary=True),
            outcome("billing", AgentOutcomeStatus.TIMEOUT),
        ],
    ))

    assert result.status is SynthesisStatus.FAILED
    assert result.escalate is True
    assert result.successful_agents == []
    assert result.failed_agents == ["technical", "billing"]


def test_synthesizer_propagates_model_detected_conflicts():
    """证明融合模型识别的冲突会传播到状态和人工升级信号。"""
    client = JsonClient({
        "answer": "两项问题需要人工核对后处理。",
        "conflicts": ["一个结果建议立即退款，另一个要求先验证身份"],
        "escalate": False,
        "reason": "operational conflict",
    })
    synthesizer = ResultSynthesizer(client=client, model="test")
    result = asyncio.run(synthesizer.synthesize(
        "question",
        [
            outcome("billing", primary=True, content="立即退款"),
            outcome("technical", content="先验证身份"),
        ],
    ))

    assert result.status is SynthesisStatus.CONFLICT
    assert result.conflicts
    assert result.escalate is True


def test_unavailable_synthesis_preserves_route_order_and_fails_closed():
    """证明融合器不可用时按路由顺序降级，并以 UNKNOWN 默认拒绝。"""
    synthesizer = ResultSynthesizer(client=MalformedClient(), model="test")
    result = asyncio.run(synthesizer.synthesize(
        "question",
        [
            outcome("technical", primary=True, content="first"),
            outcome("billing", content="second"),
        ],
    ))

    assert result.status is SynthesisStatus.UNKNOWN
    assert result.content.index("technical") < result.content.index("billing")
    assert result.escalate is True


def test_orchestrator_records_timeout_and_partial_success_in_route_order():
    """证明并行 deadline 独立生效，且 outcome 顺序稳定可诊断。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 0.01
    synthesizer = CapturingSynthesizer()
    orchestrator._result_synthesizer = synthesizer

    async def execute(_req, agent_type):
        if agent_type is AgentType.BILLING:
            await asyncio.sleep(0.05)
        return AgentResponse(
            agent_type=agent_type,
            content=f"{agent_type.value} answer",
            success=True,
            latency_ms=1.0,
        )

    orchestrator._execute = execute
    request = Request(
        message="登录失败并被重复扣款",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.TECHNICAL,
    )
    decision = RoutingDecision(
        primary_agent=AgentType.TECHNICAL,
        supporting_agents=[AgentType.BILLING],
        reason="mixed request",
        confidence=0.9,
    )

    result = asyncio.run(orchestrator.run_parallel(request, decision))

    assert [item.status for item in synthesizer.outcomes] == [
        AgentOutcomeStatus.SUCCESS,
        AgentOutcomeStatus.TIMEOUT,
    ]
    assert [item.agent_type for item in synthesizer.outcomes] == ["technical", "billing"]
    assert result.synthesis_status == "partial"
    assert result.agent_outcomes[1]["status"] == "timeout"


def test_orchestrator_converts_unhandled_agent_exception_to_typed_error():
    """证明未处理异常不会越过编排边界，而会转换为 ERROR outcome。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0

    async def execute(_req, _agent_type):
        raise RuntimeError("provider disconnected")

    orchestrator._execute = execute
    request = Request(message="help", user_id="u", conv_id="c")
    result = asyncio.run(orchestrator._execute_outcome(
        request,
        AgentType.TECHNICAL,
        is_primary=True,
    ))

    assert result.status is AgentOutcomeStatus.ERROR
    assert result.agent_type == "technical"
    assert "RuntimeError" in result.error


def test_compound_natural_request_routes_to_both_domain_owners():
    """证明自然语言复合问题会同时路由技术与账务领域 Owner。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.TECHNICAL: [object()],
        AgentType.BILLING: [object()],
    }
    request = Request(
        message="订单 #A123 登录失败后又被重复扣款 50 元",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_group="technical",
        intent_confidence=0.9,
        entities={"order_id": ["A123"], "amount": ["50 元"]},
    )

    decision = orchestrator._route_decision(request)

    assert decision.primary_agent is AgentType.TECHNICAL
    assert decision.supporting_agents == [AgentType.BILLING]
    assert decision.agent_types == [AgentType.TECHNICAL, AgentType.BILLING]


def test_single_agent_execution_uses_same_typed_timeout_boundary():
    """证明单 Agent 路径与并行路径共用同一有类型超时合同。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 0.01
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.TECHNICAL: [object()],
        AgentType.BILLING: [object()],
    }

    async def execute(_req, _agent_type):
        await asyncio.sleep(0.05)

    orchestrator._execute = execute
    request = Request(
        message="应用登录失败",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_group="technical",
        intent_confidence=0.9,
    )

    result = asyncio.run(orchestrator.run(request))

    assert result.escalated is True
    assert result.synthesis_status == "single"
    assert result.agent_outcomes[0]["status"] == "timeout"
    assert result.producer_agent_keys == []


class JsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.messages = self

    async def create(self, **_kwargs):
        return SimpleNamespace(content=[SimpleNamespace(
            type="text",
            text=json.dumps(self.payload, ensure_ascii=False),
        )])


class MalformedClient:
    def __init__(self):
        self.messages = self

    async def create(self, **_kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="not-json")])


class CapturingSynthesizer:
    def __init__(self):
        self.outcomes = []

    async def synthesize(self, _question, outcomes):
        self.outcomes = list(outcomes)
        return SynthesisResult(
            status=SynthesisStatus.PARTIAL,
            content="technical answer",
            reason="billing timed out",
            escalate=False,
            successful_agents=["technical"],
            failed_agents=["billing"],
        )
"""Agent 路由、超时与并行结果代数的核心不变量测试。"""
