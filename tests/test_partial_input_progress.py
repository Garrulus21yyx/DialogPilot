"""Input consumption is monotone and scoped to a field's original task."""
import asyncio
from dataclasses import replace
from itertools import permutations

import pytest

from application.agent_result import RequestedField
from application.conversation_state import ConversationState, ConversationStateError, PendingInteractionState, InMemoryConversationStateStore
from application.deterministic_resolution import DeterministicResolver, ResolutionKind, TurnObservations
from application.work_item import ControlMode
from tests.test_target_orchestration_runtime import _item
from tests.test_parameter_acceptance import accepted_work


def test_every_field_order_preserves_values_and_resumes_each_ready_task_once():
    a = _item('a', 'general', ControlMode.DELEGATED, 'knowledge.active_source')
    b = replace(a, work_item_id='b')
    c = replace(a, work_item_id='c', dependencies=('a',))
    values = (('a', 'choice', 'A'), ('a', 'region', 'EU'), ('b', 'choice', 'B'))
    for order in permutations(values):
        state = ConversationState.empty(tenant_id='t', user_id='u', conversation_id='c')
        state = state.wait_for_interaction(PendingInteractionState('input', 1,
            tuple(RequestedField(name, target, 'string') for target, name, _ in values), (), (a, b, c), 'thread'))
        seen = set()
        resumed = {}
        for entry in order:
            pending = state.pending_interaction
            resolution = DeterministicResolver().resolve(TurnObservations('',
                interaction_id='input', interaction_version=pending.version, interaction_values=(entry,)), state)
            assert resolution.kind is ResolutionKind.FILL_PENDING_INPUT
            seen.add(entry[:2])
            assert not set(resumed).intersection(w.work_item_id for w in resolution.resumed_work_items)
            resumed.update({w.work_item_id: w for w in resolution.resumed_work_items})
            assert ('a' in resumed) == ({('a', 'choice'), ('a', 'region')} <= seen)
            assert ('c' in resumed) == ('a' in resumed)
            assert ('b' in resumed) == (('b', 'choice') in seen)
            state = state.consume_interaction(interaction_id='input', interaction_version=pending.version, values=(entry,))
            from infrastructure.postgres_target_runtime import conversation_state_from_payload, conversation_state_to_payload
            state = conversation_state_from_payload(conversation_state_to_payload(state))
            with pytest.raises(ConversationStateError):
                state.consume_interaction(interaction_id='input', interaction_version=pending.version, values=(entry,))
            if state.pending_interaction:
                assert {(f.target_work_item_id, f.field_name) for f in state.pending_interaction.requested_fields} == {v[:2] for v in values} - seen
        assert state.pending_interaction is None
        assert {x.name: x.value for x in resumed['a'].arguments} == {'choice': 'A', 'region': 'EU'}
        assert {x.name: x.value for x in resumed['b'].arguments} == {'choice': 'B'}


@pytest.mark.parametrize('semantic', [False, True])
def test_partial_submission_without_ready_work_keeps_wait_then_resumes_with_both_values(semantic):
    from application.conversation_agent import ConversationAgent
    from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
    from application.target_conversation_manager import TargetConversationManager
    from application.orchestration_runtime import OrchestrationRuntime
    from core.identity import IdentityFactory
    from tests.test_conversation_response_contract import Provider
    state, registry, item, _ = accepted_work()
    state = replace(state, pending_interaction=replace(state.pending_interaction, requested_fields=(
        RequestedField('reply', item.work_item_id, 'string'), RequestedField('region', item.work_item_id, 'string'))))
    store = InMemoryConversationStateStore()
    store._states[('tenant-a', 'user-a', 'conversation-a')] = state
    provider = Provider({'status': 'resolved', 'input_values': [dict(target_work_item_id=item.work_item_id, field_name='reply', value='blue')]})
    manager = TargetConversationManager(state_store=store, registry=registry,
        understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(), ConversationAgent(provider)),
        orchestration=OrchestrationRuntime(direct_executor=None, domain_workers={}))
    def identity(request):
        return IdentityFactory().create_invocation(tenant_id='tenant-a', user_id='user-a', conversation_id='conversation-a', request_id=request)
    first = asyncio.run(manager.prepare(identity('partial'), TurnObservations('blue' if semantic else '',
        interaction_id='input', interaction_version=1,
        interaction_values=() if semantic else ((item.work_item_id, 'reply', 'blue'),))))
    assert first.plan.work is None
    pending = first.state.pending_interaction
    assert pending.version == 2 and [f.field_name for f in pending.requested_fields] == ['region']
    store._states[('tenant-a', 'user-a', 'conversation-a')] = first.state
    second = asyncio.run(manager.prepare(identity('rest'), TurnObservations('', interaction_id='input', interaction_version=2,
        interaction_values=((item.work_item_id, 'region', 'EU'),))))
    assert second.state.pending_interaction is None
    resumed, = second.plan.work.items
    assert {a.name: a.value for a in resumed.arguments} == {'order_id': 'DP1111', 'reply': 'blue', 'region': 'EU'}


def test_unscoped_same_name_cannot_fill_two_tasks_and_unknown_or_duplicate_cannot_consume():
    state, _, item, _ = accepted_work()
    peer = replace(item, work_item_id='peer', control=None)
    state = replace(state, pending_interaction=replace(state.pending_interaction,
        requested_fields=(RequestedField('reply', item.work_item_id, 'string'), RequestedField('reply', 'peer', 'string')),
        suspended_work_items=(item, peer)))
    result = DeterministicResolver().resolve(TurnObservations('', interaction_id='input', interaction_version=1,
        structured_fields=(('reply', 'blue'),)), state)
    assert not result.fields
    for values in ((('unknown', 'reply', 'x'),), ((item.work_item_id, 'reply', 'x'),) * 2):
        with pytest.raises(ConversationStateError):
            state.consume_interaction(interaction_id='input', interaction_version=1, values=values)


@pytest.mark.parametrize('first_owner', ['product_technical', 'order_logistics'])
def test_checkpoint_resumes_only_answered_question_and_retains_its_result(first_owner):
    from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
    from tests.test_task_result_lifecycle import _setup
    from tests.test_target_persistence_and_manager import _identity
    from collections import Counter
    async def run():
        calls = []
        async def worker(context):
            item = context.work_item
            values = {a.name: a.value for a in item.arguments}
            calls.append((item.owner_agent, values, context.trusted_context.get('resolved_input_signal')))
            if 'reply' not in values:
                return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.NEEDS_USER_INPUT, 'MISSING', 'test',
                    missing_inputs=(MissingInputSpec('reply', item.work_item_id, 'MISSING', 'string', 'Which option?'),))
            return AgentResult(item.work_item_id, item.owner_agent, AgentResultStatus.SUCCEEDED, 'DONE', 'test')
        manager, _, store = _setup(worker)
        initial = await manager.handle(_identity('initial'), TurnObservations('Both questions'))
        pending = initial.state_after.pending_interaction
        first = next(w for w in pending.suspended_work_items if w.owner_agent == first_owner)
        partial = await manager.handle(_identity('first-answer'), TurnObservations('', interaction_id=pending.interaction_id,
            interaction_version=pending.version, interaction_values=((first.work_item_id, 'reply', 'one'),)))
        pending = partial.state_after.pending_interaction
        assert pending and pending.version == 2
        assert len(pending.requested_fields) == 1 and len(pending.suspended_work_items) == 1
        assert calls[-1][0] == first_owner and calls[-1][2] == pending.interaction_id
        assert len(calls) == 3
        second = pending.suspended_work_items[0]
        complete = await manager.handle(_identity('second-answer'), TurnObservations('', interaction_id=pending.interaction_id,
            interaction_version=pending.version, interaction_values=((second.work_item_id, 'reply', 'two'),)))
        assert complete.state_after.pending_interaction is None
        assert Counter(c[0] for c in calls) == {'product_technical': 2, 'order_logistics': 2}
        assert len(complete.board.outcome_items) == 2
        assert all(r.status is AgentResultStatus.SUCCEEDED for _, r in complete.board.outcome_items)
    asyncio.run(run())
