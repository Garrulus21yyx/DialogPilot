import asyncio
from dataclasses import replace
from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    EvidenceRequest,
    FactRecord,
    FactSourceKind,
    MissingInputSpec,
)
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.orchestration_runtime import (
    AgentContextView,
    OrchestrationRuntime,
    OrchestrationRuntimeError,
)
from application.result_board import ResultBoard, ResultBoardError
from application.work_item import ArgumentValue, ControlMode, WorkItem, WorkPlan


def _item(
    work_item_id,
    owner,
    mode,
    requirement,
    *,
    dependencies=(),
):
    return WorkItem(
        work_item_id,
        owner,
        f"Handle {requirement}",
        mode,
        (f"{owner}_tool",),
        () if mode is ControlMode.DIRECT else (f"{owner}_skill",),
        (),
        (requirement,),
        dependencies,
        CapabilityEffect.READ,
        CapabilityRisk.LOW,
        "agent-result-v1",
        "default:v1",
        1,
        "registry:v1:test",
        2,
        3,
    )


def _fact(item, value):
    return FactRecord(
        f"subject:{item.work_item_id}",
        item.requirement_ids[0],
        f'{{"value":"{value}"}}',
        FactSourceKind.VERIFIED_STATE,
        f"receipt:{item.work_item_id}",
        item.allowed_tools[0],
        "v1",
        datetime.now(timezone.utc),
    )


class Executor:
    def __init__(self, calls, *, status=AgentResultStatus.SUCCEEDED, delay=0):
        self.calls = calls
        self.status = status
        self.delay = delay

    async def __call__(self, context: AgentContextView):
        self.calls.append((context.work_item.work_item_id, "start"))
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append((context.work_item.work_item_id, "end"))
        facts = (
            (_fact(context.work_item, context.work_item.work_item_id),)
            if self.status in {AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL}
            else ()
        )
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            self.status,
            self.status.value,
            "test-executor-v1",
            facts=facts,
            retryable=self.status is AgentResultStatus.RETRYABLE_FAILURE,
        )


class EvidenceSeekingWorker:
    def __init__(self):
        self.calls = []

    async def __call__(self, context: AgentContextView):
        self.calls.append(tuple(
            fact.requirement_id for fact in context.verified_facts
        ))
        model = next((
            fact for fact in context.verified_facts
            if fact.requirement_id == "product.canonical_model"
        ), None)
        if model is None:
            return AgentResult(
                context.work_item.work_item_id,
                context.work_item.owner_agent,
                AgentResultStatus.NEEDS_EVIDENCE,
                "PRODUCT_MODEL_REQUIRED",
                "evidence-worker-v1",
                requested_evidence=(EvidenceRequest(
                    "product.canonical_model",
                    context.work_item.work_item_id,
                    ("product_catalog", "media_perception"),
                ),),
            )
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "PRODUCT_ANSWER_READY",
            "evidence-worker-v1",
            facts=(_fact(context.work_item, "answer"),),
        )


class CatalogEvidenceResolver:
    def __init__(self):
        self.calls = []

    async def __call__(self, request, context):
        self.calls.append((request.requirement_id, request.preferred_providers))
        return (FactRecord(
            "product:SKU-9",
            request.requirement_id,
            '{"model":"PX-200"}',
            FactSourceKind.VERIFIED_STATE,
            "catalog-receipt:SKU-9",
            "catalog_search",
            "catalog-v1",
            datetime.now(timezone.utc),
        ),)


class MissingThenCompleteExecutor:
    def __init__(self):
        self.calls = []

    async def __call__(self, context):
        arguments = {item.name: item.value for item in context.work_item.arguments}
        self.calls.append(arguments)
        if "reference" not in arguments:
            return AgentResult(
                context.work_item.work_item_id,
                context.work_item.owner_agent,
                AgentResultStatus.NEEDS_USER_INPUT,
                "REFERENCE_REQUIRED",
                "missing-then-complete-v1",
                missing_inputs=(MissingInputSpec(
                    "reference",
                    context.work_item.work_item_id,
                    "REFERENCE_REQUIRED",
                    "string",
                    "请提供引用。",
                ),),
            )
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "REFERENCE_RESOLVED",
            "missing-then-complete-v1",
            facts=(_fact(context.work_item, str(arguments["reference"])),),
        )


def test_direct_path_executes_without_starting_a_domain_agent():
    item = _item(
        "order-1", "order_logistics", ControlMode.DIRECT, "order.current_state",
    )
    calls = []
    runtime = OrchestrationRuntime(
        direct_executor=Executor(calls),
        domain_workers={},
    )

    board = asyncio.run(runtime.execute(WorkPlan((item,), item.work_item_id), current_message="query"))

    assert board.complete is True
    assert calls == [("order-1", "start"), ("order-1", "end")]
    assert board.results[0].owner_agent == "order_logistics"


def test_single_delegated_task_invokes_only_its_domain_worker():
    item = _item(
        "product-1", "product_technical", ControlMode.DELEGATED,
        "product.canonical_model",
    )
    direct_calls = []
    product_calls = []
    runtime = OrchestrationRuntime(
        direct_executor=Executor(direct_calls),
        domain_workers={"product_technical": Executor(product_calls)},
    )

    board = asyncio.run(runtime.execute(WorkPlan((item,), item.work_item_id), current_message="image"))

    assert board.complete is True
    assert direct_calls == []
    assert product_calls[0][0] == "product-1"


def test_delegated_worker_resumes_from_system_evidence_without_user_interaction():
    item = _item(
        "product-answer-1",
        "product_technical",
        ControlMode.DELEGATED,
        "knowledge.active_source",
    )
    worker = EvidenceSeekingWorker()
    resolver = CatalogEvidenceResolver()
    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={"product_technical": worker},
        evidence_resolver=resolver,
    )

    board = asyncio.run(runtime.execute(
        WorkPlan((item,), item.work_item_id),
        current_message="回答该商品问题",
    ))

    assert board.results[0].status is AgentResultStatus.SUCCEEDED
    assert worker.calls == [(), ("product.canonical_model",)]
    assert resolver.calls == [(
        "product.canonical_model",
        ("product_catalog", "media_perception"),
    )]
    assert {
        fact.requirement_id for fact in board.facts
    } == {"product.canonical_model", "knowledge.active_source"}


def test_unresolved_evidence_remains_typed_and_does_not_loop():
    item = _item(
        "product-answer-1",
        "product_technical",
        ControlMode.DELEGATED,
        "knowledge.active_source",
    )
    worker = EvidenceSeekingWorker()

    async def no_evidence(_request, _context):
        return ()

    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={"product_technical": worker},
        evidence_resolver=no_evidence,
    )
    board = asyncio.run(runtime.execute(
        WorkPlan((item,), item.work_item_id),
        current_message="回答该商品问题",
    ))

    assert board.results[0].status is AgentResultStatus.NEEDS_EVIDENCE
    assert worker.calls == [()]


def test_checkpointed_graph_interrupts_and_resumes_the_same_work_position():
    item = _item(
        "general-1", "general", ControlMode.DELEGATED,
        "knowledge.active_source",
    )
    worker = MissingThenCompleteExecutor()
    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={"general": worker},
        checkpointer=InMemorySaver(),
    )

    async def run():
        initial = await runtime.execute(
            WorkPlan((item,), item.work_item_id),
            current_message="处理之前那个问题",
            thread_id="thread-native-resume",
        )
        snapshot = await runtime.graph.aget_state({
            "configurable": {"thread_id": "thread-native-resume"},
        })
        resumed_item = replace(
            item,
            arguments=(ArgumentValue.create("reference", "case-9"),),
        )
        resumed = await runtime.resume(
            WorkPlan((resumed_item,), resumed_item.work_item_id),
            current_message="case-9",
            thread_id="thread-native-resume",
        )
        return initial, snapshot, resumed

    initial, snapshot, resumed = asyncio.run(run())

    assert initial.results[0].status is AgentResultStatus.NEEDS_USER_INPUT
    assert any(task.interrupts for task in snapshot.tasks)
    assert resumed.results[0].status is AgentResultStatus.SUCCEEDED
    assert worker.calls == [{}, {"reference": "case-9"}]


def test_evidence_resolver_cannot_substitute_an_unrequested_requirement():
    item = _item(
        "product-answer-1",
        "product_technical",
        ControlMode.DELEGATED,
        "knowledge.active_source",
    )

    async def wrong_evidence(_request, context):
        return (_fact(context.work_item, "wrong-requirement"),)

    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={"product_technical": EvidenceSeekingWorker()},
        evidence_resolver=wrong_evidence,
    )

    with pytest.raises(
        OrchestrationRuntimeError,
        match="another requirement",
    ):
        asyncio.run(runtime.execute(
            WorkPlan((item,), item.work_item_id),
            current_message="回答该商品问题",
        ))


def test_independent_multi_domain_workers_run_in_the_same_parallel_wave():
    refund = _item(
        "refund-1", "billing_refund", ControlMode.DELEGATED, "refund.current_state",
    )
    product = _item(
        "product-1", "product_technical", ControlMode.DELEGATED,
        "product.canonical_model",
    )
    calls = []
    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={
            "billing_refund": Executor(calls, delay=0.02),
            "product_technical": Executor(calls, delay=0.02),
        },
    )

    board = asyncio.run(runtime.execute(
        WorkPlan((refund, product), refund.work_item_id), current_message="two tasks",
    ))

    assert board.complete is True
    assert [event for _, event in calls[:2]] == ["start", "start"]
    assert {item.work_item_id for item in board.results} == {"refund-1", "product-1"}


def test_dependency_failure_blocks_only_downstream_and_preserves_independent_success():
    failed = _item(
        "product-1", "product_technical", ControlMode.DELEGATED,
        "product.canonical_model",
    )
    blocked = _item(
        "product-answer-1", "product_technical", ControlMode.DELEGATED,
        "knowledge.active_source", dependencies=("product-1",),
    )
    refund = _item(
        "refund-1", "billing_refund", ControlMode.DELEGATED, "refund.current_state",
    )
    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={
            "product_technical": Executor([], status=AgentResultStatus.RETRYABLE_FAILURE),
            "billing_refund": Executor([]),
        },
    )

    board = asyncio.run(runtime.execute(
        WorkPlan((failed, blocked, refund), failed.work_item_id), current_message="compound",
    ))

    by_id = {item.work_item_id: item for item in board.results}
    assert by_id["product-1"].status is AgentResultStatus.RETRYABLE_FAILURE
    assert by_id["product-answer-1"].status is AgentResultStatus.BLOCKED
    assert by_id["refund-1"].status is AgentResultStatus.SUCCEEDED
    assert board.partial_delivery_allowed is True


def test_result_board_rejects_cross_owner_result_and_fact_conflict():
    item = _item(
        "refund-1", "billing_refund", ControlMode.DELEGATED, "refund.current_state",
    )
    plan = WorkPlan((item,), item.work_item_id)
    wrong_owner = AgentResult(
        item.work_item_id,
        "product_technical",
        AgentResultStatus.SUCCEEDED,
        "DONE",
        "test-v1",
    )
    with pytest.raises(ResultBoardError, match="owner differs"):
        ResultBoard().evaluate(plan, (wrong_owner,))

    first = AgentResult(
        item.work_item_id,
        item.owner_agent,
        AgentResultStatus.SUCCEEDED,
        "DONE",
        "test-v1",
        facts=(_fact(item, "PROCESSING"),),
    )
    other = _item(
        "refund-2", "billing_refund", ControlMode.DELEGATED, "refund.current_state",
    )
    second_fact = FactRecord(
        first.facts[0].subject_ref,
        "refund.current_state",
        '{"value":"COMPLETE"}',
        FactSourceKind.VERIFIED_STATE,
        "receipt:other",
        "billing_refund_tool",
        "v1",
        datetime.now(timezone.utc),
    )
    second = AgentResult(
        other.work_item_id,
        other.owner_agent,
        AgentResultStatus.SUCCEEDED,
        "DONE",
        "test-v1",
        facts=(second_fact,),
    )

    board = ResultBoard().evaluate(
        WorkPlan((item, other), item.work_item_id),
        (first, second),
    )
    assert board.conflict_keys == ("subject:refund-1:refund.current_state",)
    assert board.partial_delivery_allowed is False
