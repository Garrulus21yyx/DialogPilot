"""Domain handbacks enter the existing bounded planner, not a second runtime."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from application.agent_result import AgentResultStatus
from application.default_capability_registry import build_default_capability_registry
from application.execution_progress import requires_observation, planning_observations, advance_progress
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from infrastructure.target_framework_agent import TargetFrameworkAgent
from tests.test_target_framework_agent import ScriptedToolModel, _manager, _context


@pytest.mark.parametrize("reassign", [False, True])
def test_domain_can_request_reassignment_without_losing_investigation(reassign):
    from application.work_item import WorkControlBinding, WorkPlan
    from application.orchestration_runtime import OrchestrationRuntime
    from application.agent_result import AgentResult
    context = _context()
    context = replace(context, work_item=replace(context.work_item, control=WorkControlBinding("goal", 1)))
    reason = "The remaining goal requires an order specialist outside this assignment."
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "lookup", "name": "catalog_search",
            "args": {"query": "product"}}]),
        AIMessage(content="", tool_calls=[{"id": "handoff", "name": "report_blocked",
            "args": {"reason": reason, "needs_reassignment": reassign}}]),
    ])
    calls = []
    agent = TargetFrameworkAgent(model, _manager(calls), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry("tenant-a"), system_prompt="Resolve the assigned product goal.")
    result = asyncio.run(agent(context))
    assert model.calls == 2 and model.review_calls == 0
    assert len(calls) == 1 and len(result.facts) == 1
    assert result.status is (AgentResultStatus.TERMINAL_FAILURE if reassign else AgentResultStatus.BLOCKED)
    assert result.assignment_issue == (reason if reassign else None)
    assert result.pending_action is None and not result.retryable
    serde = target_checkpoint_serializer()
    assert serde.loads_typed(serde.dumps_typed(result)) == result
    # Feed the real handback into the same observation predicate and progress
    # algebra used by TurnRuntime. No new retry/coordination loop is introduced.
    board = SimpleNamespace(outcome_items=((context.work_item, result),),
        work_items=(context.work_item,), results=(result,), complete=True)
    plan = SimpleNamespace(work=True, observation_work_item_ids=())
    assert requires_observation(plan, board) is reassign
    progress = {}
    for _ in range(5):
        progress = advance_progress(progress, planning_observations(board))
    assert bool(progress.get("progress_blocked")) is reassign
    if reassign:
        received = []
        repaired = replace(context.work_item, work_item_id="order-work", owner_agent="order_logistics",
                           control=WorkControlBinding("goal", 2))
        async def next_worker(ctx):
            received.append(ctx)
            return AgentResult(ctx.work_item.work_item_id, ctx.work_item.owner_agent,
                AgentResultStatus.SUCCEEDED, "INVESTIGATION_REUSED", "test", facts=ctx.verified_facts)
        runtime = OrchestrationRuntime(direct_executor=next_worker,
            domain_workers={"order_logistics": next_worker})
        final = asyncio.run(runtime.execute(WorkPlan((repaired,), repaired.work_item_id),
            current_message=context.current_message, trusted_context=context.trusted_context,
            retained_outcomes=((context.work_item, result),)))
        assert received[0].verified_facts == result.facts
        assert received[0].working_messages == () and received[0].pending_approval is None
        assert not any(row.assignment_issue for row in final.results)


@pytest.mark.parametrize("revision_delta", [0, 1, 2])
@pytest.mark.parametrize("same_control", [False, True])
def test_cross_owner_reassignment_transfers_only_explicit_source_evidence(revision_delta, same_control):
    from dataclasses import replace
    from datetime import datetime, timezone, timedelta
    from application.agent_result import AgentResult, FactRecord, FactSourceKind
    from application.work_item import WorkPlan, WorkControlBinding
    from application.orchestration_runtime import OrchestrationRuntime
    from tests.test_target_framework_agent import _item

    original = replace(_item(), control=WorkControlBinding("goal", 1))
    next_item = replace(original, work_item_id="new-owner-work", owner_agent="order_logistics",
        control=WorkControlBinding("goal" if same_control else "other", 1 + revision_delta))
    # Two facts retain original subjects and source; the expired one is not a
    # valid input. Raw working history stays with the old domain.
    now = datetime.now(timezone.utc)
    fact = FactRecord(subject_ref="product:known", requirement_id="product.canonical_model",
        value_json='"MODEL-X"', source_kind=FactSourceKind.VERIFIED_STATE, source_ref="catalog-source",
        producer_id="catalog", producer_version="v1", observed_at=now)
    expired = replace(fact, subject_ref="expired", source_ref="old-source",
        observed_at=now-timedelta(days=2), valid_until=now-timedelta(days=1))
    result = AgentResult(original.work_item_id, original.owner_agent, AgentResultStatus.TERMINAL_FAILURE,
        "ASSIGNMENT", "test", facts=(fact, expired), assignment_issue="Need other domain",
        working_messages=({"internal": "do not transfer"},))
    received = []
    async def worker(context):
        received.append(context)
        return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
            AgentResultStatus.BLOCKED, "INSPECTED", "test")
    runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={next_item.owner_agent: worker})
    asyncio.run(runtime.execute(WorkPlan((next_item,), next_item.work_item_id), current_message="continue",
                                retained_outcomes=((original, result),)))
    assert received[0].verified_facts == ((fact,) if same_control and revision_delta == 1 else ())
    assert received[0].working_messages == ()


def test_native_worker_handback_planner_revision_and_new_owner_form_one_chain():
    from application.agent_result import AgentResult
    from application.conversation_state import InMemoryConversationStateStore
    from application.deterministic_resolution import TurnObservations
    from application.orchestration_runtime import OrchestrationRuntime
    from application.response_assembly import ResponseAssembler
    from application.target_conversation_manager import TargetConversationManager
    from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
    from application.turn_runtime import TurnRuntime
    from core.identity import IdentityFactory
    from langgraph.checkpoint.memory import InMemorySaver
    from tests.test_turn_runtime import _Executor, _OrderUnderstanding
    from tests.test_knowledge_answer_boundary import Verifier

    registry = build_default_capability_registry("tenant-a")
    registry = replace(registry, skills=(), agents=tuple(
        replace(agent, allowed_tool_ids=("catalog_search",), allowed_skill_ids=())
        if agent.agent_id == "product_technical" else agent for agent in registry.agents))
    model = ScriptedToolModel(responses=[
        AIMessage(content="", tool_calls=[{"id": "lookup", "name": "catalog_search", "args": {"query": "product"}}]),
        AIMessage(content="", tool_calls=[{"id": "handoff", "name": "report_blocked", "args": {
            "reason": "Order specialist is needed for the remaining order investigation.", "needs_reassignment": True}}]),
    ])
    calls, received = [], []
    product = TargetFrameworkAgent(model, _manager(calls), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(), registry=registry,
        system_prompt="Investigate the supplied product.")
    second_model = ScriptedToolModel(responses=[AIMessage(content="", tool_calls=[{
        "id": "second-handoff", "name": "report_blocked", "args": {
            "reason": "The remaining investigation needs general service records.",
            "needs_reassignment": True}}])])
    second_tools = _manager([], allowed_agents=("general",))
    from mcp.tool_manager import Tool
    async def unexpected_read(params, context):
        pytest.fail("reassignment should use the already supplied investigation")
    second_tools.register(Tool("order_lookup", "Read order", unexpected_read,
        {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        allowed_agents=("general",), authority="order.current_state"))
    # Match the second specialist's available read tool, without granting writes.
    registry = replace(registry, actions=(), flows=(), agents=tuple(
        replace(agent, allowed_tool_ids=("order_lookup",)) if agent.agent_id == "order_logistics"
        else agent for agent in registry.agents))
    product._registry = registry
    second = TargetFrameworkAgent(second_model, second_tools, review_model=second_model,
        review_available_tokens=14200, result_store=InMemoryStore(), registry=registry,
        system_prompt="Investigate the order goal using supplied facts.")
    class Understanding:
        async def __call__(self, observations, state, deterministic, registry, context):
            if context.observed_execution:
                failed = next(result for result in context.observed_execution.results if result.assignment_issue)
                failed_work = next(item for item in context.observed_execution.work_items
                                   if item.work_item_id == failed.work_item_id)
                source = next(control for control in state.active_work_controls
                              if control.control_id == failed_work.control.control_id)
                return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
                    "next", CommandKind.DELEGATE_TASK,
                    "order_logistics" if failed.owner_agent == "product_technical" else "general",
                    "Investigate the remaining issue",
                    allow_action_proposals=False, revises_control_id=source.control_id),), "REASSIGN")
            read = (await _OrderUnderstanding()()).commands[0]
            return TurnProposal(ProposalDisposition.RESOLVED, (read, CommandProposal(
                "product", CommandKind.DELEGATE_TASK, "product_technical", "Identify the product for the order issue",
                allow_action_proposals=False)), "INITIAL")
    async def order(context):
        received.append(context)
        assert any('PX-200' in fact.value_json for fact in context.verified_facts)
        assert context.working_messages == ()
        return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
                           AgentResultStatus.SUCCEEDED, "DONE", "test")
    async def run():
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        reads = _Executor()
        manager = TargetConversationManager(state_store=InMemoryConversationStateStore(), registry=registry,
            understanding=Understanding(), orchestration=OrchestrationRuntime(direct_executor=reads,
                domain_workers={"product_technical": product, "order_logistics": second, "general": order}, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)), checkpointer=saver)
        identity = IdentityFactory().create_invocation(tenant_id="tenant-a", user_id="user-a",
            conversation_id="collaboration", request_id="one")
        result = await runtime.execute(identity, TurnObservations("Check order status and investigate its product issue"))
        assert result.managed.request_completed
        assert len(received) == len(calls) == reads.calls == 1
        assert second_model.calls == 1
    asyncio.run(run())
