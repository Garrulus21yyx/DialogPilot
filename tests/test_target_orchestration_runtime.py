import asyncio
import itertools
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
from application.work_item import ArgumentValue, ControlMode, WorkItem, WorkItemContractError, WorkPlan


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


def test_trusted_context_preserves_structured_runtime_values_without_serializer_warnings():
    import warnings
    from pydantic import TypeAdapter
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    context = AgentContextView(_item("lookup", "retail", ControlMode.DIRECT, "order"),
        "Check shipment", (), (), (), 6000, trusted_context={
            "tenant_id": "tenant-a", "user_id": "user-a",
            "knowledge_filter_contract": {"catalog_id": "test-catalog", "sales_channels": {"web": {"enabled": True}}},
        })
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        projected = TypeAdapter(AgentContextView).dump_python(context, mode="json")
    assert projected["trusted_context"] == context.trusted_context
    serializer = target_checkpoint_serializer()
    # Parent/worker graph state checkpoints the mapping, not AgentContextView.
    restored = serializer.loads_typed(serializer.dumps_typed({"trusted_context": context.trusted_context}))
    assert restored["trusted_context"] == context.trusted_context


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


@pytest.mark.parametrize("status", [AgentResultStatus.SUCCEEDED, AgentResultStatus.TERMINAL_FAILURE])
def test_new_execution_step_consumes_prior_outcomes_without_rescheduling_them(status):
    async def scenario():
        saver = InMemorySaver()
        calls = []
        first = _item("step:0:lookup", "retail", ControlMode.DIRECT, "customer")
        second = _item("step:1:lookup", "retail", ControlMode.DIRECT, "order")
        executor = Executor(calls)
        runtime = OrchestrationRuntime(direct_executor=executor, domain_workers={}, checkpointer=saver)
        prior = await runtime.execute(WorkPlan((first,), first.work_item_id),
                                      current_message="Complete the original request", thread_id="step:0")

        async def next_executor(context):
            assert context.work_item == second
            assert prior.facts[0] in context.verified_facts
            assert context.current_message == "Complete the original request"
            return await Executor(calls, status=status)(context)

        runtime = OrchestrationRuntime(direct_executor=next_executor, domain_workers={}, checkpointer=saver)
        plan = WorkPlan((second,), second.work_item_id)
        board = await runtime.execute(plan, current_message="Complete the original request",
            thread_id="step:1", retained_outcomes=prior.outcome_items)
        assert board.retained_outcomes == prior.outcome_items
        assert prior.facts[0] in board.facts
        assert board.task_completed is (status is AgentResultStatus.SUCCEEDED)
        if status is AgentResultStatus.TERMINAL_FAILURE:
            assert board.partial_delivery_allowed
        before = list(calls)
        # Recreate the runtime: persisted outcomes and the completed second step
        # must survive without executing either tool again.
        restarted = OrchestrationRuntime(direct_executor=next_executor, domain_workers={}, checkpointer=saver)
        restored = await restarted.execute(plan, current_message="Complete the original request",
            thread_id="step:1", retained_outcomes=prior.outcome_items)
        assert restored == board
        assert calls == before
        assert len(calls) == 4
        with pytest.raises(OrchestrationRuntimeError, match="different observed outcomes"):
            await restarted.execute(plan, current_message="Complete the original request", thread_id="step:1")
    asyncio.run(scenario())


def test_resume_imports_completed_observation_from_another_execution_thread():
    async def scenario():
        saver, calls = InMemorySaver(), []
        waiting = _item("waiting", "retail", ControlMode.DIRECT, "customer")
        observed = _item("observed", "retail", ControlMode.DIRECT, "order")
        next_read = _item("next", "retail", ControlMode.DIRECT, "shipment")

        async def execute(context):
            item = context.work_item
            if item == waiting:
                calls.append((item.work_item_id, "wait"))
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    "MISSING", "test", missing_inputs=(MissingInputSpec("reference", item.work_item_id,
                        "MISSING", "string", "Which reference?"),))
            if item == next_read:
                assert any(fact.subject_ref == "subject:observed" for fact in context.verified_facts)
            return await Executor(calls)(context)

        runtime = OrchestrationRuntime(direct_executor=execute, domain_workers={}, checkpointer=saver)
        await runtime.execute(WorkPlan((waiting,), waiting.work_item_id), current_message="Waiting", thread_id="A")
        prior = await runtime.execute(WorkPlan((observed,), observed.work_item_id), current_message="Lookup", thread_id="B")
        plan = WorkPlan((next_read,), next_read.work_item_id)
        board = await runtime.resume(plan, current_message="Continue original request", thread_id="A",
            retained_outcomes=prior.outcome_items, interrupt_after_completion=True)
        assert {item.work_item_id for item, _ in board.outcome_items} == {"waiting", "observed", "next"}
        assert prior.facts[0] in board.facts
        before = list(calls)
        restored = await runtime.resume(plan, current_message="Continue original request", thread_id="A",
            retained_outcomes=prior.outcome_items, interrupt_after_completion=True)
        assert restored == board
        assert calls == before
    asyncio.run(scenario())


@pytest.mark.parametrize("expired", [False, True])
def test_bound_continuation_restores_only_valid_progress_from_checkpoint(expired):
    from dataclasses import replace
    from datetime import timedelta
    from application.work_item import WorkControlBinding
    from application.agent_result import MissingInputSpec
    from langgraph.checkpoint.memory import InMemorySaver
    item = replace(_item("progress", "product", ControlMode.DELEGATED, "product.details"), control=WorkControlBinding("goal", 1))
    fact = _fact(item, "already read")
    if expired:
        fact = replace(fact, observed_at=fact.observed_at - timedelta(days=2),
                       valid_until=fact.observed_at - timedelta(days=1))
    reads = []
    async def worker(context):
        work = context.work_item
        if work.continuation_of is None:
            reads.append("lookup")
            return AgentResult(work.work_item_id, work.owner_agent,
                AgentResultStatus.NEEDS_USER_INPUT, "INPUT", "test", facts=(fact,),
                missing_inputs=(MissingInputSpec("choice", work.work_item_id, "CHOICE", "string", "Which choice?"),))
        assert context.verified_facts == (() if expired else (fact,))
        return AgentResult(work.work_item_id, work.owner_agent,
            AgentResultStatus.SUCCEEDED, "DONE", "test",
            facts=(_fact(work, "refreshed"),) if expired else context.verified_facts)
    async def run():
        from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={"product": worker}, checkpointer=saver)
        await runtime.execute(WorkPlan((item,), item.work_item_id), current_message="Check then ask", thread_id="progress-thread")
        # A new runtime instance uses only the checkpoint, not closure-owned task results.
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={"product": worker}, checkpointer=saver)
        resumed = replace(item, work_item_id="continued", continuation_of=item.work_item_id,
                          control=WorkControlBinding("goal", 2))
        return await runtime.resume(WorkPlan((resumed,), resumed.work_item_id),
                                    current_message="blue", thread_id="progress-thread")
    board = asyncio.run(run())
    assert reads == ["lookup"]
    assert board.results[0].status is AgentResultStatus.SUCCEEDED


@pytest.mark.parametrize("queued_count", [0, 1, 3])
@pytest.mark.parametrize("checkpoint_backend", ["memory", "postgres"])
def test_closing_interrupt_retains_results_and_cancels_unstarted_work(queued_count, checkpoint_backend, request):
    from contextlib import AsyncExitStack
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    database_url = request.getfixturevalue("postgres_database_url") if checkpoint_backend == "postgres" else None
    first = replace(_item("waiting", "product", ControlMode.DELEGATED, "product.details"),
                    allowed_actions=("product.change:v1",))
    independent = _item("read", "product", ControlMode.DIRECT, "product.details")
    queued = tuple(replace(first, work_item_id=f"queued-{index}") for index in range(queued_count))
    plan = WorkPlan((first, independent, *queued), first.work_item_id)
    calls = []

    async def worker(context):
        item = context.work_item
        calls.append(item.work_item_id)
        return AgentResult(item.work_item_id, item.owner_agent,
            AgentResultStatus.WAITING_APPROVAL if item.work_item_id == first.work_item_id else AgentResultStatus.SUCCEEDED,
            "OBSERVED", "test", facts=(_fact(item, "retained"),))

    async def run():
        async with AsyncExitStack() as stack:
            saver = (await stack.enter_async_context(AsyncPostgresCheckpointOwner(database_url, setup=True))
                     if database_url else InMemorySaver(serde=target_checkpoint_serializer()))
            runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={"product": worker},
                                           checkpointer=saver)
            thread_id = f"close-queued-{queued_count}"
            before = await runtime.execute(plan, current_message="Check", thread_id=thread_id)
            await runtime.cancel_interrupt(thread_id=thread_id)
            await runtime.cancel_interrupt(thread_id=thread_id)
            snapshot = await runtime.graph.aget_state({"configurable": {"thread_id": thread_id}})
            assert not snapshot.next
            after = snapshot.values["board"]
            assert after.complete
            assert tuple(after.facts) == before.facts
            assert tuple(after.results[:2]) == before.results
            assert all(result.status is AgentResultStatus.CANCELLED for result in after.results[2:])
    asyncio.run(run())
    assert sorted(calls) == ["read", "waiting"]


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


class DependencyCapturingExecutor:
    def __init__(self):
        self.dependencies = ()

    async def __call__(self, context):
        self.dependencies = context.dependency_results
        return AgentResult(
            context.work_item.work_item_id,
            context.work_item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "DEPENDENCY_CONTEXT_RECEIVED",
            "dependency-capture-v1",
            facts=(_fact(context.work_item, "done"),),
        )


@pytest.mark.parametrize("status", tuple(AgentResultStatus))
def test_runtime_statistics_preserve_typed_outcomes_without_inventing_quality(status):
    item = _item("metrics-1", "general", ControlMode.DIRECT, "general.answer")

    async def worker(context):
        return AgentResult(
            item.work_item_id, item.owner_agent, status, status.value, "metrics-test-v1",
            missing_inputs=(MissingInputSpec(
                "reference", item.work_item_id, "REQUIRED", "string", "请提供引用",
            ),) if status is AgentResultStatus.NEEDS_USER_INPUT else (),
            requested_evidence=(EvidenceRequest(
                "general.answer", item.work_item_id, ("knowledge",),
            ),) if status is AgentResultStatus.NEEDS_EVIDENCE else (),
            retryable=status is AgentResultStatus.RETRYABLE_FAILURE,
        )

    runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={})
    asyncio.run(runtime._execute_work_item({
        "work_item": item, "current_message": "查询", "facts": (),
        "recent_relevant_turns": (), "evidence_refs": (), "token_budget": 1000,
    }))
    snapshot = runtime.get_stats()["general"]
    assert snapshot["total"] == 1
    assert snapshot["outcome_counts"] == {status.value: 1}
    assert snapshot["avg_ms"] >= 0
    assert "quality_score" not in snapshot
    if status is AgentResultStatus.SUCCEEDED:
        assert snapshot["success_rate"] == 1
    elif status in {
        AgentResultStatus.PARTIAL, AgentResultStatus.RETRYABLE_FAILURE,
        AgentResultStatus.TERMINAL_FAILURE,
    }:
        assert snapshot["success_rate"] == 0
    else:
        assert snapshot["outcome_samples"] == 0
        assert snapshot["success_rate"] is None
    snapshot["outcome_counts"].clear()
    assert runtime.get_stats()["general"]["outcome_counts"] == {status.value: 1}


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


def test_result_board_is_invariant_to_parallel_completion_order():
    items = tuple(
        _item(f"work-{index}", f"owner-{index}", ControlMode.DIRECT, f"fact.{index}")
        for index in range(3)
    )
    plan = WorkPlan(items, items[0].work_item_id)
    results = tuple(
        AgentResult(
            item.work_item_id,
            item.owner_agent,
            AgentResultStatus.SUCCEEDED,
            "DONE",
            "test-v1",
            facts=(_fact(item, str(index)),),
        )
        for index, item in enumerate(items)
    )

    snapshots = tuple(
        ResultBoard().evaluate(plan, permutation)
        for permutation in itertools.permutations(results)
    )

    expected_result_ids = tuple(item.work_item_id for item in items)
    expected_fact_ids = tuple(item.requirement_ids[0] for item in items)
    assert all(
        tuple(result.work_item_id for result in snapshot.results)
        == expected_result_ids
        for snapshot in snapshots
    )
    assert all(
        tuple(fact.requirement_id for fact in snapshot.facts)
        == expected_fact_ids
        for snapshot in snapshots
    )
    assert all(snapshot == snapshots[0] for snapshot in snapshots[1:])


@pytest.mark.parametrize("status", list(AgentResultStatus))
def test_result_completion_is_not_a_success_claim(status):
    item = _item("work", "general", ControlMode.DIRECT, "fact.required")
    result = AgentResult(
        "work", "general", status, "OBSERVED", "test-v1",
        missing_inputs=(MissingInputSpec("reference", "work", "REQUIRED", "string", "Reference?"),)
        if status is AgentResultStatus.NEEDS_USER_INPUT else (),
        requested_evidence=(EvidenceRequest("fact.required", "work", ("fixture",)),)
        if status is AgentResultStatus.NEEDS_EVIDENCE else (),
        retryable=status is AgentResultStatus.RETRYABLE_FAILURE,
    )
    board = ResultBoard().evaluate(WorkPlan((item,), "work"), (result,))
    assert board.complete
    assert board.results[0].status is status
    assert board.missing_requirement_ids == ("fact.required",)


@pytest.mark.parametrize("extra_kind", ["duplicate", "outside"])
def test_result_board_rejects_invalid_extensions_without_changing_valid_prefix(extra_kind):
    items = tuple(_item(str(i), "general", ControlMode.DIRECT, f"fact.{i}") for i in range(3))
    plan = WorkPlan(items, "0")
    results = tuple(AgentResult(item.work_item_id, "general", AgentResultStatus.SUCCEEDED,
                               "DONE", "test-v1", facts=(_fact(item, "ok"),)) for item in items)
    for permutation in itertools.permutations(results):
        for length in range(1, 4):
            prefix = permutation[:length]
            snapshot = ResultBoard().evaluate(plan, prefix)
            extra = prefix[0] if extra_kind == "duplicate" else replace(prefix[0], work_item_id="outside")
            with pytest.raises(ResultBoardError):
                ResultBoard().evaluate(plan, (*prefix, extra))
            assert ResultBoard().evaluate(plan, prefix) == snapshot
            assert snapshot.complete is (length == 3)


@pytest.mark.parametrize("length", range(2, 9))
def test_work_plan_accepts_dependency_chains_and_rejects_dangling_edges_and_cycles(length):
    items = tuple(_item(str(i), "general", ControlMode.DIRECT, f"fact.{i}",
                        dependencies=(str(i - 1),) if i else ()) for i in range(length))
    for ordered in (items, tuple(reversed(items))):
        plan = WorkPlan(ordered, "0")
        assert tuple(item.work_item_id for item in ResultBoard().evaluate(plan, ()).ready_items) == ("0",)
    for target in ("missing", str(length - 1)):
        invalid = (replace(items[0], dependencies=(target,)), *items[1:])
        with pytest.raises(WorkItemContractError):
            WorkPlan(invalid, "0")


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


def test_worker_receives_only_its_declared_dependency_results():
    first = _item(
        "order-1", "order_logistics", ControlMode.DIRECT,
        "order.current_state",
    )
    second = _item(
        "handoff-1", "human_service", ControlMode.DELEGATED,
        "support.handoff_action", dependencies=(first.work_item_id,),
    )
    capture = DependencyCapturingExecutor()
    runtime = OrchestrationRuntime(
        direct_executor=Executor([]),
        domain_workers={"human_service": capture},
    )

    board = asyncio.run(runtime.execute(
        WorkPlan((first, second), first.work_item_id),
        current_message="check then hand off",
    ))

    assert board.complete is True
    assert tuple(
        result.work_item_id for result in capture.dependencies
    ) == (first.work_item_id,)


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
