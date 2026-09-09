"""Proposal decisions survive goal continuation without becoming goal cancellation."""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.action_approval import action_decision_context, merge_action_decisions
from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
from application.conversation_state import ConversationStateConflict
from application.deterministic_resolution import ResolutionKind
from application.orchestration_runtime import OrchestrationRuntime
from application.work_item import ArgumentValue, WorkControlBinding, WorkPlan
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from infrastructure.target_action_preparation import TargetActionPreparation
from tests.test_approval_conversation import domain


def decision(kind='DECLINED'):
    return {'approval_id': 'approval', 'action_ref': 'order.cancel:v1',
            'arguments': {'order_id': 'DP1234', 'expected_order_version': 1},
            'control_id': 'goal', 'decision': kind}


@pytest.mark.parametrize('kind,approved,expected', [
    (ResolutionKind.APPROVAL_DECISION, False, 'DECLINED'),
    (ResolutionKind.APPROVAL_DECISION, True, 'APPROVED'),
    (ResolutionKind.APPROVAL_EXPIRED, False, 'EXPIRED'),
])
def test_decision_is_exact_idempotent_consumed_proposal(kind, approved, expected):
    pending = SimpleNamespace(approval_id='approval', action_ref='order.cancel:v1',
        arguments=(ArgumentValue.create('order_id', 'DP1234'),
                   ArgumentValue.create('expected_order_version', 1)),
        origin_control=WorkControlBinding('goal', 1))
    resolution = SimpleNamespace(kind=kind, approved=approved)
    records = action_decision_context((), pending, resolution)
    assert records == (decision(expected),)
    assert action_decision_context(records, pending, resolution) == records
    assert action_decision_context(records, None, resolution) == records
    with pytest.raises(ConversationStateConflict):
        merge_action_decisions(records, ({**records[0], 'arguments': {'order_id': 'other'}},))


@pytest.mark.parametrize('kind', ['DECLINED', 'EXPIRED', 'APPROVED'])
@pytest.mark.parametrize('same_target', [False, True])
def test_preparation_respects_decision_scope_without_treating_expiry_as_rejection(kind, same_target):
    agent, context, _, calls = domain([])
    context = replace(context, work_item=replace(context.work_item,
        work_item_id='continued', continuation_of='original', control=WorkControlBinding('goal', 2)),
        trusted_context={**context.trusted_context, 'action_decisions': (decision(kind),)})
    result = asyncio.run(TargetActionPreparation(agent._registry, agent._tool_manager).prepare(
        context, 'order.cancel:v1', {'order_id': 'DP1234' if same_target else 'DP5678'}, 'candidate'))
    blocked = kind == 'DECLINED' and same_target
    assert bool(result.pending_action) is not blocked
    assert len(calls) == int(not blocked)
    if blocked:
        assert result.reason_code == 'ACTION_PREVIOUSLY_DECLINED'


@pytest.mark.parametrize('import_source', [False, True])
@pytest.mark.parametrize('revised', [False, True])
@pytest.mark.parametrize('backend', ['memory', 'postgres'])
def test_decision_survives_two_resumes_but_not_explicit_goal_replacement(import_source, revised, backend, request):
    from uuid import uuid4
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    async def run(saver):
        agent, context, _, calls = domain([])
        original = replace(context.work_item, work_item_id='original', control=WorkControlBinding('goal', 1))
        seen = []
        async def worker(ctx):
            seen.append(ctx)
            item = ctx.work_item
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                'INPUT', 'test', missing_inputs=(MissingInputSpec('reply', item.work_item_id,
                    'INPUT', 'string', 'Which remaining option?'),))
        def runtime():
            return OrchestrationRuntime(direct_executor=worker,
                domain_workers={original.owner_agent: worker}, checkpointer=saver)
        scope = {**context.trusted_context, 'tenant_id': 'tenant', 'user_id': 'user',
                 'conversation_id': 'conversation', 'conv_id': 'conversation'}
        primary = uuid4().hex
        source = uuid4().hex if import_source else primary
        await runtime().execute(WorkPlan((original,), 'original'), current_message='Initial',
            thread_id=source, trusted_context={**scope, 'action_decisions': (decision(),)})
        if import_source:
            other = replace(original, work_item_id='other', control=WorkControlBinding('other', 1))
            await runtime().execute(WorkPlan((other,), 'other'), current_message='Other',
                thread_id=primary, trusted_context=scope)
        second = replace(original, work_item_id='second', control=WorkControlBinding('goal', 2),
                         continuation_of=None if revised else 'original')
        await runtime().resume(WorkPlan((second,), 'second'), current_message='Continue',
            thread_id=primary, source_thread_ids=(source,) if import_source else (), trusted_context=scope)
        third = replace(second, work_item_id='third', continuation_of='second', control=WorkControlBinding('goal', 3))
        await runtime().resume(WorkPlan((third,), 'third'), current_message='The missing option',
            thread_id=primary, trusted_context=scope)
        assert seen[-1].trusted_context['action_decisions'] == (() if revised else (decision(),))
        result = await TargetActionPreparation(agent._registry, agent._tool_manager).prepare(
            seen[-1], 'order.cancel:v1', {'order_id': 'DP1234'}, 'candidate')
        assert bool(result.pending_action) is revised
        assert len(calls) == int(revised)
    if backend == 'memory':
        asyncio.run(run(InMemorySaver(serde=target_checkpoint_serializer())))
    else:
        url = request.getfixturevalue('postgres_database_url')
        async def postgres():
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver)
        asyncio.run(postgres())


@pytest.mark.parametrize('edge_mask', range(64))
def test_decline_and_explicit_revision_close_descendants_of_any_dag_node(edge_mask):
    from itertools import combinations
    from application.target_understanding import StateBoundTargetUnderstanding
    from application.turn_planning import CommandKind, CommandProposal, TurnProposal, ProposalDisposition
    agent, context, _, _ = domain([])
    edges = [edge for bit, edge in enumerate(combinations(range(4), 2)) if edge_mask & (1 << bit)]
    items = tuple(replace(context.work_item, work_item_id=str(n),
        control=WorkControlBinding(str(n), 1),
        dependencies=tuple(str(a) for a, b in edges if b == n)) for n in range(4))
    state = SimpleNamespace(active_work_controls=tuple(item.control for item in items),
        pending_approval=SimpleNamespace(origin_control=items[0].control,
            suspended_work_items=items), pending_interaction=None)
    bound = TurnProposal(ProposalDisposition.RESOLVED,
        StateBoundTargetUnderstanding._continuations(items, state), 'DECLINED')
    resolution = SimpleNamespace(approved=False, resumed_work_items=items)
    reach = {(a, b): (a, b) in edges for a in range(4) for b in range(4)}
    for via in range(4):
        for a in range(4):
            for b in range(4):
                reach[a, b] = reach[a, b] or (reach[a, via] and reach[via, b])
    for target in range(4):
        for cancel in (False, True):
            command = CommandProposal('explicit', CommandKind.CANCEL_WORK if cancel else CommandKind.DELEGATE_TASK,
                items[target].owner_agent, 'Revised goal', revises_control_id=str(target),
                **({} if cancel else {'allow_action_proposals': False}))
            merged = StateBoundTargetUnderstanding.merge_approval_plan(
                TurnProposal(ProposalDisposition.RESOLVED, (command,), 'SEMANTIC'), bound, state, resolution)
            closed = {c.revises_control_id for c in merged.commands if c.kind is CommandKind.CANCEL_WORK}
            assert closed == {str(n) for n in range(4) if reach[target, n] or (cancel and n == target)}
            executable = {c.command_id for c in merged.commands if c.kind is not CommandKind.CANCEL_WORK}
            assert all(set(c.dependencies) <= executable for c in merged.commands)
            assert {c.revises_control_id for c in merged.commands} == {'0', '1', '2', '3'}
            from application.target_conversation_manager import TargetConversationManager
            plan = SimpleNamespace(control_mutations=tuple(SimpleNamespace(control_id=key) for key in closed),
                work=SimpleNamespace(items=tuple(replace(items[int(c.revises_control_id)],
                    control=WorkControlBinding(c.revises_control_id, 2), continuation_of=c.continuation_of)
                    for c in merged.commands if c.kind is not CommandKind.CANCEL_WORK)))
            projected = TargetConversationManager._closed_work_items(state, resolution, plan)
            assert {item.work_item_id for item in projected} == {
                str(n) for n in range(4) if n == target or reach[target, n]}

            # Multiple explicit commands must obey the same dependency closure.
            from application.turn_planning import TurnPlanningError
            for child in range(4):
                if child == target:
                    continue
                continuation = StateBoundTargetUnderstanding._continuations((items[child],), state)[0]
                semantic = TurnProposal(ProposalDisposition.RESOLVED, (command, continuation), 'SEMANTIC')
                if reach[target, child]:
                    with pytest.raises(TurnPlanningError, match='prerequisite'):
                        StateBoundTargetUnderstanding.merge_approval_plan(semantic, bound, state, resolution)
                else:
                    combined = StateBoundTargetUnderstanding.merge_approval_plan(semantic, bound, state, resolution)
                    executable = {c.command_id for c in combined.commands if c.kind is not CommandKind.CANCEL_WORK}
                    assert all(set(c.dependencies) <= executable for c in combined.commands)


@pytest.mark.parametrize('target', [0, 1, 2])
def test_closed_goal_projection_retires_unstarted_descendants_in_checkpoint(target):
    async def run():
        from application.action_approval import partition_work_revision
        _, context, _, _ = domain([])
        items = tuple(replace(context.work_item, work_item_id=str(n),
            control=WorkControlBinding(str(n), 1), dependencies=(str(n - 1),) if n else ()) for n in range(3))
        async def worker(ctx):
            item = ctx.work_item
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                'INPUT', 'test', missing_inputs=(MissingInputSpec('reply', item.work_item_id,
                    'INPUT', 'string', 'Choose'),))
        runtime = OrchestrationRuntime(direct_executor=worker, domain_workers={items[0].owner_agent: worker},
            checkpointer=InMemorySaver(serde=target_checkpoint_serializer()))
        await runtime.execute(WorkPlan(items, '0'), current_message='Initial', thread_id='thread')
        closed, _ = partition_work_revision(items, {str(target)})
        result = await runtime.cancel_interrupt(thread_id='thread', closed_work_items=closed, retain_wait=True)
        statuses = {r.work_item_id: r.status for r in result.all_results}
        assert all(statuses[str(n)] is AgentResultStatus.CANCELLED for n in range(target, 3))
        if target:
            assert statuses['0'] is AgentResultStatus.NEEDS_USER_INPUT
    asyncio.run(run())
