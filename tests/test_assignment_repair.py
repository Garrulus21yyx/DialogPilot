"""Assignment corrections return to their owner, retaining independent work."""
import asyncio
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from application.agent_result import AgentResult, AgentResultStatus
from application.conversation_context import conversation_context_payload
from application.conversation_state import InMemoryConversationStateStore
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_conversation_manager import TargetConversationManager
from application.turn_planning import CommandKind, CommandProposal, ProposalDisposition, TurnProposal
from application.turn_runtime import TurnRuntime
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_knowledge_answer_boundary import Verifier
from tests.test_turn_runtime import _Executor, _OrderUnderstanding, _identity


@pytest.mark.parametrize('repair', [False, True, 'unavailable'])
@pytest.mark.parametrize('backend', ['memory', 'postgres'])
def test_assignment_feedback_preserves_source_siblings_and_bounds_replanning(repair, backend, request):
    async def run(saver, store):
        requests, contexts = [], []
        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                requests.append(observations.raw_text)
                if context.observed_execution:
                    outcomes = conversation_context_payload(context)['observed_execution']['outcomes']
                    assert any(row['assignment_issue'] for row in outcomes)
                    assert any(row['reason_code'] == 'ORDER_FOUND' for row in outcomes)
                    if repair == 'unavailable':
                        from application.turn_planning import PlanningUnavailable
                        raise PlanningUnavailable(ProposalDisposition.PROVIDER_FAILURE, 'PLANNER_UNAVAILABLE')
                    original = next(control for control in state.active_work_controls
                                    if control.owner_agent == 'order_logistics' and control.objective == 'Identify only')
                    return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal(
                        'corrected', CommandKind.DELEGATE_TASK, 'order_logistics',
                        'Handle the requested order change' if repair else 'Identify only',
                        allow_action_proposals=repair, revises_control_id=original.control_id),), 'REPAIR')
                read = (await _OrderUnderstanding()()).commands[0]
                return TurnProposal(ProposalDisposition.RESOLVED, (read, CommandProposal(
                    'narrow', CommandKind.DELEGATE_TASK, 'order_logistics', 'Identify only',
                    allow_action_proposals=False)), 'INITIAL')

        async def worker(context):
            contexts.append(context)
            item = context.work_item
            if not item.allowed_actions:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.TERMINAL_FAILURE,
                    'ASSIGNMENT_REPAIR_REQUIRED', 'test', assignment_issue='Requested change omitted')
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED,
                'DONE', 'test')

        reads = _Executor()
        manager = TargetConversationManager(state_store=store,
            registry=build_default_capability_registry('tenant-a'), understanding=Understanding(),
            orchestration=OrchestrationRuntime(direct_executor=reads,
                domain_workers={'order_logistics': worker}, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)),
            checkpointer=saver, max_observation_steps=8)
        from core.identity import IdentityFactory
        from uuid import uuid4
        identity = IdentityFactory().create_invocation(tenant_id='tenant-a', user_id='user-a',
            conversation_id='assignment-' + uuid4().hex, request_id='request')
        message = TurnObservations('Check status and handle the change')
        result = await runtime.execute(identity, message)
        assert reads.calls == 1
        assert requests == [message.raw_text] * (2 if repair else 4)
        assert len(contexts[0].trusted_context['assignment_view']['assignments']) == 2
        assert result.managed.board.facts  # independent read is not discarded
        assert result.managed.request_completed is (repair is True)
        if repair is not True:
            assert result.managed.diagnostics[-1].detail['code'] == (
                'PLANNER_UNAVAILABLE' if repair == 'unavailable' else 'OBSERVATION_NO_PROGRESS')
            assert not result.managed.request_completed
            issue = next(r for r in result.managed.board.all_results if r.assignment_issue)
            serde = target_checkpoint_serializer()
            assert serde.loads_typed(serde.dumps_typed(issue)) == issue
            from infrastructure.target_agent_result_adapter import framework_artifact, restore_framework_artifact
            assert restore_framework_artifact(framework_artifact(issue)) == issue
        count = len(contexts)
        await runtime.execute(identity, message)
        assert len(contexts) == count and reads.calls == 1
    if backend == 'memory':
        asyncio.run(run(InMemorySaver(serde=target_checkpoint_serializer()), InMemoryConversationStateStore()))
    else:
        from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
        from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
        from infrastructure.postgres_target_runtime import PostgresConversationStateStore
        url = request.getfixturevalue('postgres_database_url')
        PostgresMigrationRunner(url).upgrade()
        pool = PostgresPool(PostgresPoolConfig(url, min_size=1, max_size=2))
        pool.open()
        async def postgres():
            async with AsyncPostgresCheckpointOwner(url, setup=True) as saver:
                await run(saver, PostgresConversationStateStore(pool))
        try:
            asyncio.run(postgres())
        finally:
            pool.close()


@pytest.mark.parametrize('waiting', ['approval', 'input'])
def test_assignment_repair_runs_beside_wait_without_resuming_or_replacing_it(waiting):
    from application.agent_result import MissingInputSpec
    from tests.test_approval_conversation import domain, call
    async def run():
        domain_agent, _, _, tool_calls = domain([call('prepare_order_cancel')])
        registry = domain_agent._registry
        seen, approval_ids = [], []
        class Understanding:
            async def __call__(self, observations, state, deterministic, registry, context):
                if context.observed_execution:
                    approval_ids.append(state.pending_approval.approval_id if state.pending_approval else None)
                    control = next(c for c in state.active_work_controls if c.objective == 'Wrong scope')
                    return TurnProposal(ProposalDisposition.RESOLVED, (CommandProposal('repair',
                        CommandKind.DELEGATE_TASK, 'order_logistics', 'Correct scope',
                        allow_action_proposals=False, revises_control_id=control.control_id),), 'REPAIR')
                commands = [CommandProposal('waiting', CommandKind.DELEGATE_TASK, 'order_logistics',
                    'Waiting scope', allow_action_proposals=waiting == 'approval'),
                    CommandProposal('wrong', CommandKind.DELEGATE_TASK, 'order_logistics',
                    'Wrong scope', allow_action_proposals=False)]
                if waiting == 'approval':
                    commands.append(CommandProposal('queued', CommandKind.DELEGATE_TASK,
                        'order_logistics', 'Queued scope', allow_action_proposals=True))
                return TurnProposal(ProposalDisposition.RESOLVED, tuple(commands), 'INITIAL')
        async def worker(context):
            item = context.work_item
            seen.append(item.objective)
            if item.objective == 'Waiting scope':
                if waiting == 'approval':
                    return await domain_agent(context)
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT,
                    'MISSING', 'test', missing_inputs=(MissingInputSpec('choice', item.work_item_id,
                        'MISSING', 'string', 'Which option?'),))
            if item.objective == 'Wrong scope':
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.TERMINAL_FAILURE,
                    'ASSIGNMENT_REPAIR_REQUIRED', 'test', assignment_issue='Wrong assigned scope')
            assert item.objective == 'Correct scope'
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, 'DONE', 'test')
        saver = InMemorySaver(serde=target_checkpoint_serializer())
        store = InMemoryConversationStateStore()
        manager = TargetConversationManager(state_store=store, registry=registry,
            understanding=Understanding(), orchestration=OrchestrationRuntime(direct_executor=_Executor(),
                domain_workers={'order_logistics': worker}, checkpointer=saver))
        runtime = TurnRuntime(manager, ResponseAssembler(knowledge_verifier=Verifier(True)), checkpointer=saver)
        result = await runtime.execute(_identity(), TurnObservations('Two independent goals'))
        assert seen.count('Waiting scope') == seen.count('Wrong scope') == seen.count('Correct scope') == 1
        assert 'Queued scope' not in seen
        state = result.managed.state_after
        pending = state.pending_approval if waiting == 'approval' else state.pending_interaction
        assert pending
        assert all(item.objective != 'Wrong scope' for item in pending.suspended_work_items)
        if waiting == 'approval':
            assert approval_ids == [pending.approval_id]
            assert len(tool_calls) == 1
        assert not result.managed.request_completed
    asyncio.run(run())
