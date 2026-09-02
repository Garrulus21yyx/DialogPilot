import asyncio
import json
from types import SimpleNamespace

import pytest

from agents.agent_orchestrator import (
    AgentOrchestrator,
    AgentResponse,
    AgentType,
    EscalationAgent,
    PlanningDecision,
    PlanningDisposition,
    Request,
)
from agents.orchestration_contracts import (
    DependencyInput,
    ExecutionBudget,
    PendingSignalKind,
    PriorOutcomeBinding,
    TaskEffect,
    TaskPlan,
    TaskRisk,
    TaskSpec,
)
from agents.task_policies import MultiAgentExecutionPolicy
from application.route_decision import RequestShape, RouteMode
from core.intent_recognizer import IntentCategory, UrgencyLevel
from memory.context import ContextAssembler, ContextSection
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
            objective=f"处理 {agent_type.value} 子任务",
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


def test_synthesizer_does_not_publish_partial_required_coverage_as_synthesized():
    """Required coverage 不完整时保留诊断内容，但不声称已融合完成。"""
    synthesizer = ResultSynthesizer(client=None, model="test")
    result = asyncio.run(synthesizer.synthesize(
        "question",
        task_plan(AgentType.TECHNICAL, AgentType.BILLING),
        [
            outcome("technical", primary=True, content="technical answer"),
            outcome("billing", AgentOutcomeStatus.TIMEOUT),
        ],
    ))

    assert result.status is SynthesisStatus.UNKNOWN
    assert "technical answer" in result.content
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
            objective="处理登录故障",
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
    assert plan.tasks[1].depends_on == ("account_security_task",)
    assert [[task.task_id for task in wave] for wave in plan.execution_waves()] == [
        ["account_security_task"],
        ["billing_task"],
    ]


def test_task_graph_rejects_dangling_dependency_and_cycle_before_execution():
    """TaskGraph 在 Worker 调用前封闭悬空边与有向环。"""
    with pytest.raises(ValueError, match="dangling"):
        TaskPlan(
            tasks=(TaskSpec(
                "billing_task",
                AgentType.BILLING,
                "处理扣款",
                depends_on=("missing_task",),
            ),),
            primary_task_id="billing_task",
        )

    with pytest.raises(ValueError, match="cycle"):
        TaskPlan(
            tasks=(
                TaskSpec("technical_task", AgentType.TECHNICAL, "排障", depends_on=("billing_task",)),
                TaskSpec("billing_task", AgentType.BILLING, "核对", depends_on=("technical_task",)),
            ),
            primary_task_id="technical_task",
        )


def test_task_graph_parallelizes_independent_reads_but_serializes_writes():
    """同波次只读任务可并行，写任务在同一调度 Owner 中稳定串行。"""
    async def run_with(effect):
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator._agent_timeout_s = 1.0
        orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
        orchestrator._result_synthesizer = CapturingSynthesizer()
        active = 0
        maximum = 0

        async def execute(_req, agent_type):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return AgentResponse(agent_type=agent_type, content="ok", success=True)

        orchestrator._execute = execute
        graph = TaskPlan(
            tasks=(
                TaskSpec("technical_task", AgentType.TECHNICAL, "排障", effect=effect),
                TaskSpec("billing_task", AgentType.BILLING, "核对", effect=effect),
            ),
            primary_task_id="technical_task",
        )
        await orchestrator.run_parallel(Request(message="mixed", user_id="u", conv_id="c"), graph)
        return maximum

    assert asyncio.run(run_with(TaskEffect.READ_ONLY)) == 2
    assert asyncio.run(run_with(TaskEffect.WRITE_REQUIRES_APPROVAL)) == 1


def test_failed_dependency_blocks_downstream_without_calling_worker():
    """上游不成功时下游产生有类型终态，不偷跑 Worker。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
    orchestrator._result_synthesizer = CapturingSynthesizer()
    called = []

    async def execute(_req, agent_type):
        called.append(agent_type)
        return AgentResponse(
            agent_type=agent_type,
            content="failed",
            success=False,
            allow_fallback=False,
        )

    orchestrator._execute = execute
    graph = TaskPlan(
        tasks=(
            TaskSpec("security_task", AgentType.ACCOUNT_SECURITY, "安全止损"),
            TaskSpec("billing_task", AgentType.BILLING, "账务核对", depends_on=("security_task",)),
        ),
        primary_task_id="security_task",
    )

    result = asyncio.run(orchestrator.run_parallel(
        Request(message="被盗且扣款", user_id="u", conv_id="c"),
        graph,
    ))

    assert called == [AgentType.ACCOUNT_SECURITY]
    assert [item["status"] for item in result.agent_outcomes] == [
        "error",
        "blocked_dependency",
    ]


def test_worker_receives_only_task_evidence_and_declared_context_refs():
    """范围投影同时剪裁当前输入、section、历史和实体。"""
    assembler = ContextAssembler(
        max_input_tokens=1200,
        reserved_output_tokens=100,
        fixed_system_reserve=100,
    )
    prompt_context = assembler.assemble(
        sections=(
            ContextSection("knowledge", "401 表示认证失败"),
            ContextSection("user_profile", "VIP 账务画像"),
        ),
        history=({"role": "user", "content": "过去的扣款对话"},),
        current_user_message="登录 401 并且重复扣款 50 元",
    )
    captured = {}
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0

    async def execute(scoped, agent_type):
        captured["request"] = scoped
        return AgentResponse(agent_type=agent_type, content="ok", success=True)

    orchestrator._execute = execute
    task = TaskSpec(
        "technical_task",
        AgentType.TECHNICAL,
        "排查 401",
        evidence_spans=("登录 401",),
        context_refs=("knowledge", "entity:error_code"),
    )
    request = Request(
        message="登录 401 并且重复扣款 50 元",
        user_id="u",
        conv_id="c",
        prompt_context=prompt_context,
        context=prompt_context.system_context,
        history=[{"role": "user", "content": "过去的扣款对话"}],
        entities={"error_code": ["401"], "amount": ["50 元"]},
    )

    asyncio.run(orchestrator._execute_outcome(request, task, is_primary=True))

    scoped = captured["request"]
    assert scoped.message == "登录 401"
    assert "401 表示认证失败" in scoped.prompt_context.system_context
    assert "VIP 账务画像" not in scoped.prompt_context.system_context
    assert scoped.prompt_context.history == ()
    assert scoped.history is None
    assert scoped.entities == {"error_code": ["401"]}


def test_default_worker_contracts_include_active_ticket_authority():
    request = Request(message="继续处理上次的问题", user_id="u", conv_id="c")

    for agent_type in AgentType:
        task = AgentOrchestrator._task_for_agent(request, agent_type)
        assert "active_tickets" in task.context_refs


def test_planner_slices_compound_evidence_and_marks_explicit_refund_write():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()],
        AgentType.TECHNICAL: [object()],
        AgentType.BILLING: [object()],
    }
    compound = Request(
        message="订单 A123 登录报 401 后又被重复扣款 50 元",
        user_id="u",
        conv_id="c",
        intent=IntentCategory.TECHNICAL_LOGIN,
        intent_confidence=0.95,
    )
    graph = orchestrator._build_task_plan(compound)
    evidence = {task.owner: task.evidence_spans for task in graph.tasks}

    assert any("401" in span for span in evidence[AgentType.TECHNICAL])
    assert all("扣款" not in span for span in evidence[AgentType.TECHNICAL])
    assert any("扣款" in span for span in evidence[AgentType.BILLING])

    refund_task = orchestrator._task_for_agent(
        Request(message="帮我申请退款", user_id="u", conv_id="c"),
        AgentType.BILLING,
    )
    assert refund_task.effect is TaskEffect.WRITE_REQUIRES_APPROVAL


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


def test_high_confidence_other_terminates_out_of_scope_without_a_worker():
    """业务外请求必须由 Planner 确定性收口，不能再生成 general_task。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)

    async def must_not_execute(*_args, **_kwargs):
        raise AssertionError("out-of-scope request must not execute a worker")

    orchestrator._execute = must_not_execute
    request = Request(
        message="六边形有几条边？",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.OTHER,
        intent_group="other",
        intent_confidence=0.95,
    )

    decision = asyncio.run(orchestrator.plan(request))
    result = asyncio.run(orchestrator.run(request))

    assert decision.disposition is PlanningDisposition.OUT_OF_SCOPE
    assert decision.task_plan is None
    assert decision.agent_types == []
    assert result.routing_disposition is PlanningDisposition.OUT_OF_SCOPE
    assert result.task_plan == {}
    assert result.agent_outcomes == []
    assert result.agent_type is None
    assert result.agent_types == []
    assert result.primary_agent is None
    assert result.synthesis_status == "policy"
    assert "客服范围" in result.response


def test_low_confidence_other_terminates_with_clarification_without_a_worker():
    """语义不足与高置信度越域是两个不同终态，但都不能伪造 Worker。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    request = Request(
        message="看看",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.OTHER,
        intent_group="other",
        intent_confidence=0.2,
    )

    decision = asyncio.run(orchestrator.plan(request))
    result = asyncio.run(orchestrator.run(request))

    assert decision.disposition is PlanningDisposition.CLARIFY
    assert decision.clarification_required is True
    assert decision.task_plan is None
    assert result.routing_disposition is PlanningDisposition.CLARIFY
    assert result.agent_outcomes == []
    assert result.primary_agent is None
    assert "请补充" in result.response


def test_greeting_remains_an_executable_general_agent_request():
    """业务边界不能把正常寒暄误当越域拒答。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {AgentType.GENERAL: [object()]}
    request = Request(
        message="你好",
        user_id="user",
        conv_id="conversation",
        intent=IntentCategory.GREETING,
        intent_group="greeting",
        intent_confidence=0.95,
    )

    decision = asyncio.run(orchestrator.plan(request))

    assert decision.disposition is PlanningDisposition.EXECUTE
    assert decision.task_plan is not None
    assert decision.task_plan.primary_agent is AgentType.GENERAL


def test_planning_decision_rejects_task_and_disposition_split_brain():
    """EXECUTE 必须有图，策略终态必须无图，避免两个权威事实并存。"""
    plan = task_plan(AgentType.GENERAL)
    with pytest.raises(ValueError, match="requires a task plan"):
        PlanningDecision(
            intent=IntentCategory.QUERY,
            task_plan=None,
            disposition=PlanningDisposition.EXECUTE,
        )
    with pytest.raises(ValueError, match="cannot contain a task plan"):
        PlanningDecision(
            intent=IntentCategory.OTHER,
            task_plan=plan,
            disposition=PlanningDisposition.OUT_OF_SCOPE,
        )


def test_same_owner_split_tasks_execute_the_graph_without_becoming_multi_agent():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
    orchestrator._result_synthesizer = CapturingSynthesizer()
    tasks = (
        TaskSpec("billing-read", AgentType.BILLING, "read"),
        TaskSpec(
            "billing-write", AgentType.BILLING, "write",
            effect=TaskEffect.WRITE_REQUIRES_APPROVAL,
        ),
    )
    plan = TaskPlan(tasks, "billing-read")
    called = []

    async def planning(_request):
        return PlanningDecision(IntentCategory.BILLING, plan)

    async def execute(scoped, agent_type):
        called.append(scoped.assigned_task.task_id)
        return AgentResponse(agent_type=agent_type, content="ok", success=True)

    orchestrator.plan = planning
    orchestrator._execute = execute
    result = asyncio.run(orchestrator.run(Request(
        message="query then write", user_id="u", conv_id="c",
        intent=IntentCategory.BILLING,
    )))
    assert plan.multi_agent is False
    assert called == ["billing-read", "billing-write"]
    assert len(result.agent_outcomes) == 2


@pytest.mark.parametrize("receipt_schema", ["agent-candidate-v1", "wrong-v1"])
def test_downstream_consumes_declared_artifact_or_is_blocked(receipt_schema):
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
    orchestrator._result_synthesizer = CapturingSynthesizer()
    captured = []
    graph = TaskPlan((
        TaskSpec("upstream", AgentType.ACCOUNT_SECURITY, "secure"),
        TaskSpec(
            "downstream", AgentType.BILLING, "bill",
            depends_on=("upstream",),
            dependency_inputs=(DependencyInput(
                "upstream", "agent_candidate", receipt_schema,
            ),),
        ),
    ), "upstream")

    async def execute(scoped, agent_type):
        captured.append((scoped.assigned_task.task_id, scoped.dependency_artifacts))
        return AgentResponse(agent_type=agent_type, content="owned result", success=True)

    orchestrator._execute = execute
    result = asyncio.run(orchestrator.run_parallel(
        Request(message="security billing", user_id="u", conv_id="c"), graph,
    ))
    if receipt_schema == "agent-candidate-v1":
        assert [item[0] for item in captured] == ["upstream", "downstream"]
        assert captured[1][1][0].content == "owned result"
        assert captured[1][1][0].schema_version == "agent-candidate-v1"
    else:
        assert [item[0] for item in captured] == ["upstream"]
        assert result.agent_outcomes[1]["status"] == "blocked_dependency"


@pytest.mark.parametrize(("may_interrupt", "expected_max"), [(False, 2), (True, 1)])
def test_parallel_cap_and_interrupt_boundary_are_enforced(may_interrupt, expected_max):
    async def exercise():
        orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
        orchestrator._agent_timeout_s = 1.0
        orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
        orchestrator._task_execution_policy = MultiAgentExecutionPolicy(
            max_parallel_workers=2,
        )
        orchestrator._result_synthesizer = CapturingSynthesizer()
        active = maximum = 0

        async def execute(_scoped, agent_type):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return AgentResponse(agent_type=agent_type, content="ok", success=True)

        orchestrator._execute = execute
        graph = TaskPlan(tuple(
            TaskSpec(
                f"task-{index}", owner, "read", may_interrupt=may_interrupt,
            )
            for index, owner in enumerate((
                AgentType.GENERAL, AgentType.TECHNICAL, AgentType.BILLING,
            ))
        ), "task-0")
        await orchestrator.run_parallel(
            Request(message="multi", user_id="u", conv_id="c"), graph,
        )
        return maximum

    assert asyncio.run(exercise()) == expected_max


def test_synthesis_invokes_model_only_for_complete_non_template_multi_outcome():
    class CountingSynthesizer(ResultSynthesizer):
        def __init__(self):
            super().__init__(client=object(), model="test")
            self.calls = 0

        async def _synthesize_with_model(self, question, successful):
            self.calls += 1
            return {"answer": "merged", "conflicts": [], "reason": "merged"}

    async def mode(tasks, outcomes):
        synth = CountingSynthesizer()
        result = await synth.synthesize(
            "question", TaskPlan(tuple(tasks), tasks[0].task_id), outcomes,
        )
        return synth.calls, result

    a = TaskSpec("a", AgentType.BILLING, "a")
    b = TaskSpec("b", AgentType.TECHNICAL, "b")
    successes = (
        AgentOutcome("a", True, "billing", AgentOutcomeStatus.SUCCESS, True, content="a"),
        AgentOutcome("b", True, "technical", AgentOutcomeStatus.SUCCESS, False, content="b"),
    )
    calls, result = asyncio.run(mode((a, b), successes))
    assert (calls, result.status) == (1, SynthesisStatus.SUCCESS)

    templates = (
        TaskSpec(**{**a.__dict__, "deterministic_assembly": True}),
        TaskSpec(**{**b.__dict__, "deterministic_assembly": True}),
    )
    calls, result = asyncio.run(mode(templates, successes))
    assert (calls, result.status) == (0, SynthesisStatus.SUCCESS)

    conflict = AgentOutcome(
        **{**successes[1].__dict__, "authority_conflicts": ["state drift"]}
    )
    calls, result = asyncio.run(mode((a, b), (successes[0], conflict)))
    assert (calls, result.status) == (0, SynthesisStatus.CONFLICT)


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


def test_waiting_approval_is_native_signal_not_terminal_outcome():
    """等待外部审批不污染 task_outcomes，Coverage 只投影 AWAITING_SIGNAL。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)

    class MustNotSynthesize:
        async def synthesize(self, *_args, **_kwargs):
            raise AssertionError("pending signal must block synthesis")

    orchestrator._result_synthesizer = MustNotSynthesize()

    async def execute(_req, agent_type):
        return AgentResponse(
            agent_type=agent_type, content="等待审批", success=False,
            react_status="waiting_approval", react_run_id="run-1",
            pending_approval_call_ids=["call-1"], allow_fallback=False,
        )

    orchestrator._execute = execute
    task = TaskSpec(
        "billing-write", AgentType.BILLING, "refund",
        effect=TaskEffect.WRITE_REQUIRES_APPROVAL, may_interrupt=True,
    )
    result = asyncio.run(orchestrator.run_parallel(
        Request(message="refund", user_id="u", conv_id="c", request_id="req-1"),
        TaskPlan((task,), task.task_id),
    ))

    assert result.agent_outcomes == []
    assert result.synthesis_status == "awaiting_signal"
    assert result.pending_signals == [{
        "kind": PendingSignalKind.APPROVAL.value,
        "signal_id": "approval:run-1:billing-write",
        "task_id": "billing-write",
        "version": "pending-signal-v1",
        "workflow_run_id": "run-1",
        "payload_schema_version": "approval-decision-v1",
    }]
    assert result.coverage["projection"] == "AWAITING_SIGNAL(approval)"
    assert result.coverage["awaiting_signal_task_ids"] == ["billing-write"]
    assert result.coverage["failed_task_ids"] == []
    assert result.awaiting_approval is True


def test_first_serial_interrupt_stops_later_interrupt_capable_tasks():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
    orchestrator._result_synthesizer = ResultSynthesizer(client=None, model="test")
    called = []

    async def execute(scoped, agent_type):
        called.append(scoped.assigned_task.task_id)
        return AgentResponse(
            agent_type=agent_type, content="wait", success=False,
            react_status="waiting_approval", react_run_id="run-first",
            pending_approval_call_ids=["call-first"], allow_fallback=False,
        )

    orchestrator._execute = execute
    tasks = tuple(TaskSpec(
        f"write-{index}", owner, "write",
        effect=TaskEffect.WRITE_REQUIRES_APPROVAL, may_interrupt=True,
    ) for index, owner in enumerate((AgentType.BILLING, AgentType.ACCOUNT_SECURITY)))
    result = asyncio.run(orchestrator.run_parallel(
        Request(message="writes", user_id="u", conv_id="c"),
        TaskPlan(tasks, tasks[0].task_id),
    ))

    assert called == ["write-0"]
    assert [item["task_id"] for item in result.pending_signals] == ["write-0"]
    assert result.coverage["missing_task_ids"] == ["write-1"]


def test_concurrent_interrupt_contract_violation_fails_closed():
    """即使 Planner 错标为可并行，v1 也只保留一个 blocking signal。"""
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0
    orchestrator._execution_budget = ExecutionBudget(1.0, 1.0, 3)
    orchestrator._result_synthesizer = ResultSynthesizer(client=None, model="test")

    async def execute(scoped, agent_type):
        task_id = scoped.assigned_task.task_id
        return AgentResponse(
            agent_type=agent_type, content="wait", success=False,
            react_status="waiting_approval", react_run_id=f"run-{task_id}",
            pending_approval_call_ids=[f"call-{task_id}"], allow_fallback=False,
        )

    orchestrator._execute = execute
    tasks = (
        TaskSpec("a", AgentType.BILLING, "a"),
        TaskSpec("b", AgentType.TECHNICAL, "b"),
    )
    result = asyncio.run(orchestrator.run_parallel(
        Request(message="bad plan", user_id="u", conv_id="c"),
        TaskPlan(tasks, "a"),
    ))

    assert [item["task_id"] for item in result.pending_signals] == ["a"]
    assert result.agent_outcomes[0]["task_id"] == "b"
    assert result.agent_outcomes[0]["status"] == "error"
    assert "UNSUPPORTED_CONCURRENT_INTERRUPT" in result.agent_outcomes[0]["error"]
    assert result.coverage["complete"] is False


@pytest.mark.parametrize("terminal_status", ["cancelled", "expired"])
def test_cancelled_and_expired_are_terminal_coverage_outcomes(terminal_status):
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0

    async def execute(_req, agent_type):
        return AgentResponse(
            agent_type=agent_type, content="", success=False,
            terminal_outcome_status=terminal_status, allow_fallback=False,
        )

    orchestrator._execute = execute
    task = TaskSpec("task", AgentType.GENERAL, "task")
    terminal = asyncio.run(orchestrator._execute_outcome(
        Request(message="x", user_id="u", conv_id="c"), task, is_primary=True,
    ))
    coverage = CoverageGate.evaluate(TaskPlan((task,), "task"), (terminal,))

    assert terminal.status.value == terminal_status
    assert coverage.failed_task_ids == ("task",)
    assert coverage.missing_task_ids == ()


def test_unknown_terminal_status_is_typed_error():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._agent_timeout_s = 1.0

    async def execute(_req, agent_type):
        return AgentResponse(
            agent_type=agent_type, content="", success=False,
            terminal_outcome_status="future_status", allow_fallback=False,
        )

    orchestrator._execute = execute
    terminal = asyncio.run(orchestrator._execute_outcome(
        Request(message="x", user_id="u", conv_id="c"),
        TaskSpec("task", AgentType.GENERAL, "task"), is_primary=True,
    ))

    assert terminal.status is AgentOutcomeStatus.ERROR
    assert terminal.error == "INVALID_TERMINAL_STATUS: future_status"


def test_prior_outcome_binding_is_read_only_input_not_current_outcome():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    binding = PriorOutcomeBinding(
        task_id="prior-billing", requirement_ids=("refund.current_state",),
        artifact_refs=("artifact:prior",), evidence_receipt_refs=("receipt:prior",),
        producer_version="task-outcome-v1",
    )
    request = Request(
        message="new delta", user_id="u", conv_id="c",
        prior_outcome_bindings=(binding,),
    )
    task = TaskSpec("current", AgentType.BILLING, "current delta")

    scoped = orchestrator._scoped_request(request, task)

    assert scoped.prior_outcome_bindings == (binding,)
    assert scoped.assigned_task is task
    assert scoped.dependency_artifacts == ()


def test_canonical_route_decision_reuses_agent_domain_owner_selection():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        owner: [object()] for owner in (
            AgentType.GENERAL, AgentType.TECHNICAL, AgentType.BILLING,
            AgentType.ACCOUNT_SECURITY,
        )
    }
    request = Request(
        message="我的退款状态", user_id="u", conv_id="c",
        intent=IntentCategory.REFUND, intent_group="billing",
        urgency=UrgencyLevel.LOW,
        intent_confidence=0.95, intent_input_fingerprint="a" * 64,
    )

    decision = asyncio.run(orchestrator.decide_route(
        request, RequestShape.BUSINESS_STATE,
    ))
    task_plan = orchestrator._build_task_plan(request)

    assert decision.mode is RouteMode.AGENT_TASK
    assert decision.owner_ids == ("billing",)
    assert tuple(owner.value for owner in task_plan.agent_types) == decision.owner_ids
    assert len(request.routing_policy_trace.domain_decisions) == 1


def test_knowledge_route_skips_domain_and_instance_selection():
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._pool = {
        AgentType.GENERAL: [object()], AgentType.BILLING: [object()],
    }
    request = Request(
        message="退款政策是什么", user_id="u", conv_id="c",
        intent=IntentCategory.QUERY, intent_group="query",
        urgency=UrgencyLevel.LOW,
        intent_confidence=0.95, intent_input_fingerprint="b" * 64,
    )

    decision = asyncio.run(orchestrator.decide_route(
        request, RequestShape.KNOWLEDGE_FAQ,
    ))

    assert decision.mode is RouteMode.KNOWLEDGE_QA
    assert decision.owner_ids == ()
    assert request.routing_policy_trace.domain_decisions == []
    assert request.routing_policy_trace.instance_decisions == []
