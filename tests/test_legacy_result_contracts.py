"""Independent legacy result contracts still used by evaluation fixtures."""


import asyncio


import json


from types import SimpleNamespace


import pytest


from agents.orchestration_contracts import AgentType


from agents.orchestration_contracts import TaskPlan, TaskSpec


from services.result_synthesizer import AgentOutcome, AgentOutcomeStatus, CoverageGate, ResultSynthesizer, SynthesisStatus


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


class MalformedClient:
    def __init__(self):
        self.messages = self

    async def create(self, **_kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="not-json")])
