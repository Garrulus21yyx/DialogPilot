import asyncio
import json
from types import SimpleNamespace

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentResponse,
    AgentType,
    EscalationAgent,
    Request,
)
from agents.orchestration_contracts import ExecutionBudget, TaskPlan, TaskRisk, TaskSpec
from core.intent_recognizer import IntentCategory
from services.result_synthesizer import (
    AgentOutcome,
    AgentOutcomeStatus,
    CoverageGate,
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
        task_id=f"{agent_type}_task",
        required=True,
        agent_type=agent_type,
        responding_agent_type=agent_type,
        status=status,
        is_primary=primary,
        content=content if status is AgentOutcomeStatus.SUCCESS else "",
        error="failure" if status is not AgentOutcomeStatus.SUCCESS else "",
    )


def task_plan(*agent_types: AgentType) -> TaskPlan:
    """为融合器测试创建与 outcomes 一一对应的最小任务合同。"""
    tasks = tuple(
        TaskSpec(
            task_id=f"{agent_type.value}_task",
            owner=agent_type,
            instruction=f"处理 {agent_type.value} 子任务",
        )
        for agent_type in agent_types
    )
    return TaskPlan(tasks=tasks, primary_task_id=tasks[0].task_id)


def test_human_handoff_executes_the_registered_escalation_owner():
    """证明 ESCALATION Task 不再由 GeneralAgent 代执行。"""
    escalation = EscalationAgent(TextClient("已整理风险和待核实项，正在转人工处理。"), "test")
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {AgentType.ESCALATION: [escalation]}
    orchestrator._agent_timeout_s = 1.0
    request = Request(
        message="我要转人工处理这个异常扣款",
        user_id="u",
        conv_id="c",
        intent=IntentCategory.HUMAN_HANDOFF,
        intent_confidence=0.99,
    )

    result = asyncio.run(orchestrator.run(request))

    assert result.primary_agent is AgentType.ESCALATION
    assert result.agent_type is AgentType.ESCALATION
    assert result.agent_outcomes[0]["responding_agent_type"] == "escalation"
    assert result.escalated is True
    assert escalation._react_engine is None


def test_synthesizer_returns_partial_success_without_discarding_valid_answer():
    """证明部分失败时保留有效回答，同时返回 PARTIAL 并升级。"""
    synthesizer = ResultSynthesizer(client=None, model="test")
    result = asyncio.run(synthesizer.synthesize(
        "question",
        task_plan(AgentType.TECHNICAL, AgentType.BILLING),
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
        task_plan(AgentType.TECHNICAL, AgentType.BILLING),
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
        task_plan(AgentType.BILLING, AgentType.TECHNICAL),
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
        task_plan(AgentType.TECHNICAL, AgentType.BILLING),
        [
            outcome("technical", primary=True, content="first"),
            outcome("billing", content="second"),
        ],
    ))

    assert result.status is SynthesisStatus.UNKNOWN
    assert result.content.index("technical") < result.content.index("billing")
    assert result.escalate is True


def test_coverage_gate_rejects_missing_required_task_even_when_one_agent_succeeds():
    """证明一个漂亮回答不能掩盖另一个必需子任务完全没有 outcome。"""
    plan = task_plan(AgentType.TECHNICAL, AgentType.BILLING)

    coverage = CoverageGate.evaluate(
        plan,
        [outcome("technical", primary=True, content="technical answer")],
    )

    assert coverage.complete is False
    assert coverage.completed_task_ids == ("technical_task",)
    assert coverage.missing_task_ids == ("billing_task",)
    assert coverage.unresolved_required_task_ids == ("billing_task",)


def test_coverage_gate_rejects_duplicate_outcome_for_the_same_task():
    """证明同一任务重复返回不会被误算成两项工作均已完成。"""
    plan = task_plan(AgentType.TECHNICAL)

    coverage = CoverageGate.evaluate(
        plan,
        [outcome("technical"), outcome("technical")],
    )

    assert coverage.complete is False
    assert coverage.duplicate_task_ids == ("technical_task",)


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
    decision = task_plan(AgentType.TECHNICAL, AgentType.BILLING)
    decision = TaskPlan(
        tasks=decision.tasks,
        primary_task_id=decision.primary_task_id,
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
        TaskSpec(
            task_id="technical_task",
            owner=AgentType.TECHNICAL,
            instruction="处理登录故障",
            risk=TaskRisk.MEDIUM,
        ),
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

    decision = orchestrator._build_task_plan(request)

    assert decision.primary_agent is AgentType.TECHNICAL
    assert decision.supporting_agents == [AgentType.BILLING]
    assert decision.agent_types == [AgentType.TECHNICAL, AgentType.BILLING]


def test_compound_colloquial_charge_expression_routes_to_billing_support():
    """证明“重复扣了”这类口语变体也属于账务领域正证据。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.TECHNICAL: [object()],
        AgentType.BILLING: [object()],
    }
    request = Request(
        message="订单 A123 登录报 401 后又重复扣了 50 元",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_confidence=0.95,
        entities={"order_id": ["A123"], "error_code": ["401"], "amount": ["50 元"]},
    )

    plan = orchestrator._build_task_plan(request)

    assert plan.agent_types == [AgentType.TECHNICAL, AgentType.BILLING]


def test_negated_billing_term_does_not_create_billing_task():
    """证明“不是扣款问题”不会被关键词层误当作 Billing 正证据。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.TECHNICAL: [object()],
        AgentType.BILLING: [object()],
    }
    request = Request(
        message="不是扣款问题，只是登录 401",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_group="technical",
        intent_confidence=0.95,
        entities={"error_code": ["401"]},
    )

    plan = orchestrator._build_task_plan(request)

    assert plan.agent_types == [AgentType.TECHNICAL]
    assert plan.primary_task_id == "technical_task"


def test_account_security_is_the_owner_and_billing_is_only_supporting():
    """证明账号被盗不再误归 Billing，异常扣款仍由账务能力并行补充。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.TECHNICAL: [object()],
        AgentType.BILLING: [object()],
        AgentType.ACCOUNT_SECURITY: [object()],
    }
    request = Request(
        message="账号被盗，而且出现了一笔重复扣款",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.ACCOUNT_SECURITY,
        intent_group="account",
        intent_confidence=0.95,
    )

    plan = orchestrator._build_task_plan(request)

    assert plan.primary_agent is AgentType.ACCOUNT_SECURITY
    assert plan.supporting_agents == [AgentType.BILLING]
    assert plan.primary_task.risk is TaskRisk.HIGH


def test_max_agent_budget_keeps_plan_but_emits_typed_budget_outcome():
    """证明并发上限不会偷偷删除任务，而会留下可覆盖检查的预算终态。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(
        request_timeout_s=1.0,
        agent_timeout_s=1.0,
        max_agents=1,
    )
    orchestrator._result_synthesizer = CapturingSynthesizer()

    async def execute(_req, agent_type):
        return AgentResponse(agent_type=agent_type, content="answer", success=True)

    orchestrator._execute = execute
    request = Request(message="登录失败并重复扣款", user_id="u", conv_id="c")
    plan = task_plan(AgentType.TECHNICAL, AgentType.BILLING)

    result = asyncio.run(orchestrator.run_parallel(request, plan))

    assert [item["status"] for item in result.agent_outcomes] == [
        "success",
        "budget_exceeded",
    ]
    assert result.coverage["unresolved_required_task_ids"] == ["billing_task"]


def test_react_failure_evidence_reaches_typed_agent_outcome():
    """证明工具拒绝不会丢成普通异常，编排结果保留 ReAct 与 call_id 证据。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0

    async def execute(_req, agent_type):
        return AgentResponse(
            agent_type=agent_type,
            content="高风险工具等待人工审批。",
            success=False,
            escalate=True,
            error="tool call requires approval",
            react_status="blocked",
            react_steps=2,
            tool_call_ids=["call-risk"],
            allow_fallback=False,
        )

    orchestrator._execute = execute
    task = task_plan(AgentType.BILLING).primary_task
    result = asyncio.run(orchestrator._execute_outcome(
        Request(message="退款", user_id="u", conv_id="c"),
        task,
        is_primary=True,
    ))

    assert result.status is AgentOutcomeStatus.ERROR
    assert result.react_status == "blocked"
    assert result.react_steps == 2
    assert result.tool_call_ids == ["call-risk"]
    assert result.escalate is True


def test_request_deadline_bounds_all_parallel_workers():
    """证明多个 Worker 共享请求 deadline，而不是各自重新获得完整超时。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(
        request_timeout_s=0.01,
        agent_timeout_s=1.0,
        max_agents=2,
    )
    orchestrator._result_synthesizer = ResultSynthesizer(client=None, model="test")

    async def execute(_req, _agent_type):
        await asyncio.sleep(0.05)

    orchestrator._execute = execute
    request = Request(message="登录失败并重复扣款", user_id="u", conv_id="c")

    result = asyncio.run(orchestrator.run_parallel(
        request,
        task_plan(AgentType.TECHNICAL, AgentType.BILLING),
    ))

    assert {item["status"] for item in result.agent_outcomes} == {"budget_exceeded"}
    assert result.escalated is True
    assert result.coverage["complete"] is False


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


class TextClient:
    def __init__(self, text):
        self.text = text
        self.messages = self

    async def create(self, **_kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)])


class MalformedClient:
    def __init__(self):
        self.messages = self

    async def create(self, **_kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="not-json")])


class CapturingSynthesizer:
    def __init__(self):
        self.outcomes = []

    async def synthesize(self, _question, plan, outcomes):
        self.outcomes = list(outcomes)
        return SynthesisResult(
            status=SynthesisStatus.PARTIAL,
            content="technical answer",
            reason="billing timed out",
            escalate=False,
            coverage=CoverageGate.evaluate(plan, outcomes),
            successful_agents=["technical"],
            failed_agents=["billing"],
        )
"""Agent 路由、超时与并行结果代数的核心不变量测试。"""
