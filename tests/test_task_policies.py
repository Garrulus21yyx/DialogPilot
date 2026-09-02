"""M2-T06A versioned TaskFormation/Execution/Synthesis policy invariants."""
import pytest

from agents.orchestration_contracts import (
    AgentType,
    DependencyInput,
    TaskEffect,
    TaskRisk,
    TaskSpec,
)
from agents.task_policies import (
    MultiAgentExecutionPolicy,
    SynthesisInvocationPolicy,
    SynthesisMode,
    TaskFormationPolicy,
    TaskPolicyError,
    pinned_config_ref,
)


def _task(task_id, owner=AgentType.BILLING, **changes):
    values = dict(
        task_id=task_id, owner=owner, objective=task_id,
        risk=TaskRisk.HIGH, context_refs=("history", "knowledge"),
        requirement_ids=(f"requirement.{task_id}",),
    )
    values.update(changes)
    return TaskSpec(**values)


def _policies():
    return TaskFormationPolicy(), MultiAgentExecutionPolicy(), SynthesisInvocationPolicy()


def _form(tasks, primary):
    formation, execution, synthesis = _policies()
    return formation.form(
        tasks, primary_task_id=primary,
        execution_policy_version=execution.version,
        synthesis_policy_version=synthesis.version,
        pinned_config_ref=pinned_config_ref(formation, execution, synthesis),
    )


def test_same_owner_compatible_read_requirements_coalesce_into_one_worker_task():
    plan = _form((_task("refund_state"), _task("refund_eligibility")), "refund_state")
    assert len(plan.tasks) == 1
    assert plan.tasks[0].requirement_ids == (
        "requirement.refund_state", "requirement.refund_eligibility",
    )
    assert plan.multi_agent is False
    assert plan.fingerprint == plan.to_dict()["plan_fingerprint"]


@pytest.mark.parametrize(
    "second",
    [
        _task("write", effect=TaskEffect.WRITE_REQUIRES_APPROVAL),
        _task("permission", permission_scope="billing:admin"),
        _task("interrupt", may_interrupt=True),
        _task("owner", owner=AgentType.TECHNICAL),
    ],
)
def test_effect_permission_interrupt_and_owner_boundaries_split_tasks(second):
    plan = _form((_task("read"), second), "read")
    assert len(plan.tasks) == 2
    assert plan.multi_agent is (second.owner is not AgentType.BILLING)


def test_versioned_dependency_requires_typed_input_and_changes_plan_fingerprint():
    upstream = _task("upstream", deterministic_assembly=True)
    downstream = _task(
        "downstream", owner=AgentType.TECHNICAL,
        depends_on=("upstream",),
        dependency_inputs=(DependencyInput(
            "upstream", "evidence_receipt", "evidence-receipt-v1",
        ),),
    )
    plan = _form((upstream, downstream), "upstream")
    changed = _form((upstream, TaskSpec(
        **{
            **downstream.__dict__,
            "dependency_inputs": (DependencyInput(
                "upstream", "artifact_ref", "artifact-ref-v1",
            ),),
        }
    )), "upstream")
    assert plan.fingerprint != changed.fingerprint

    with pytest.raises(ValueError, match="typed inputs"):
        _form((upstream, TaskSpec(
            **{**downstream.__dict__, "dependency_inputs": ()}
        )), "upstream")


def test_execution_selection_replays_waves_and_is_dependency_closed():
    root_b = _task("root-b")
    root_a = _task("root-a", owner=AgentType.TECHNICAL)
    dependent = _task(
        "dependent", owner=AgentType.ACCOUNT_SECURITY,
        depends_on=("root-b",),
        dependency_inputs=(DependencyInput(
            "root-b", "evidence_receipt", "evidence-receipt-v1",
        ),),
    )
    fourth = _task("fourth", owner=AgentType.GENERAL)
    plan = _form((root_b, root_a, dependent, fourth), "root-b")
    selection = MultiAgentExecutionPolicy().select(plan)
    assert [task.task_id for task in selection.selected] == [
        "root-b", "root-a", "fourth",
    ]
    assert [task.task_id for task in selection.deferred] == ["dependent"]


def test_plan_above_safety_boundary_fails_typed_without_truncation():
    tasks = tuple(
        _task(f"task-{index}", owner=list(AgentType)[index % len(AgentType)])
        for index in range(5)
    )
    plan = _form(tasks, "task-0")
    with pytest.raises(TaskPolicyError) as rejected:
        MultiAgentExecutionPolicy().select(plan)
    assert rejected.value.code == "PLAN_TOO_LARGE"


def test_synthesis_policy_has_closed_zero_one_template_llm_conflict_algebra():
    template_a = _task("a", deterministic_assembly=True)
    template_b = _task(
        "b", owner=AgentType.TECHNICAL, deterministic_assembly=True,
    )
    plan = _form((template_a, template_b), "a")
    policy = SynthesisInvocationPolicy()
    assert policy.decide(
        plan, successful_task_ids=(), coverage_complete=False,
    ) is SynthesisMode.NONE
    assert policy.decide(
        plan, successful_task_ids=("a",), coverage_complete=True,
    ) is SynthesisMode.DIRECT
    assert policy.decide(
        plan, successful_task_ids=("a", "b"), coverage_complete=True,
    ) is SynthesisMode.DETERMINISTIC
    semantic = _form((template_a, TaskSpec(
        **{**template_b.__dict__, "deterministic_assembly": False}
    )), "a")
    assert policy.decide(
        semantic, successful_task_ids=("a", "b"), coverage_complete=True,
    ) is SynthesisMode.LLM
    assert policy.decide(
        semantic, successful_task_ids=("a", "b"), coverage_complete=True,
        authority_conflicts=("refund state conflict",),
    ) is SynthesisMode.CONFLICT
