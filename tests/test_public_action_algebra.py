"""Public text is independent of authorization and never wins over execution."""
import asyncio
from copy import deepcopy
from itertools import permutations

import pytest
from jsonschema import ValidationError, validate
from langchain_core.messages import AIMessage

from application.conversation_actions import action_proposal, planning_actions
from application.conversation_agent import planning_output_schema, ConversationProviderOutputError
from tests.test_conversation_actions import payload, calls, provider


@pytest.mark.parametrize('decision', [None, 'approve', 'decline', 'hold'])
@pytest.mark.parametrize('work', [None, 'read', 'input', 'delegate'])
@pytest.mark.parametrize('answer', [None, 'Done.', '先等等，我还有问题。'])
def test_batch_product_and_permutation_preserves_authority(decision, work, answer):
    data = payload()
    data['pending_approval'] = {'approval_id': 'bound'}
    data['pending_input'] = {'requested_fields': [
        {'target_work_item_id': 'w', 'field_name': 'choice', 'value_schema': 'string'}]}
    parts = []
    if decision:
        parts.append(('review_action', {'decision': decision}))
    if work:
        parts.append({'read': ('knowledge_search', {'query': 'General policy'}),
            'input': ('supply_input', {'values': {'field_1': 'blue'}}),
            'delegate': ('delegate_task', {'target_agent': 'order_logistics',
                'objective': 'Investigate another item', 'allow_action_proposals': False})}[work])
    original = deepcopy(data)
    baseline = None
    for batch in permutations(calls(*parts)):
        if not work and decision in {None, 'hold'} and answer is None:
            with pytest.raises((ValueError, ValidationError)):
                action_proposal(planning_actions(data), batch, '')
            continue
        result = action_proposal(planning_actions(data), batch, answer or '')
        validate(result, planning_output_schema())
        if baseline is None:
            baseline = result
        assert result == baseline
        assert result.get('approval_decision') == (
            {'approval_id': 'bound', 'decision': decision} if decision else None)
        executing = work is not None or decision in {'approve', 'decline'}
        assert result['status'] == ('resolved' if executing else 'respond')
        assert ('response' in result) is not executing
        if not executing:
            assert result['response'] == answer
        assert data == original


def test_text_does_not_reduce_existing_read_capacity():
    data = payload()
    data['atomic_reads'] = [{'owner_agent': 'order_logistics', 'tool_id': 'read_object',
        'description': 'Read an object.', 'input_schema': {'type': 'object',
        'properties': {'id': {'type': 'integer'}}, 'required': ['id'], 'additionalProperties': False}}]
    batch = calls(*[('read_object', {'id': i}) for i in range(6)],
        ('bind_read_goals', {'bindings': [{'atomic_call': 1, 'goal_id': 'first'}]}))
    actions = planning_actions(data)
    before = action_proposal(actions, batch, '')
    assert action_proposal(actions, batch, 'All done!') == before


@pytest.mark.parametrize('part', [
    ('review_action', {'decision': 'approve'})])
def test_duplicate_terminals_are_not_last_writer_wins(part):
    data = {**payload(), 'pending_approval': {'approval_id': 'a'}}
    with pytest.raises(ValueError, match='planning_duplicate'):
        action_proposal(planning_actions(data), calls(part, part), '')


@pytest.mark.parametrize('decision', ['approve', 'decline', 'hold'])
def test_decision_schema_has_no_hidden_text_condition(decision):
    action = next(a for a in planning_actions({**payload(), 'pending_approval': {'approval_id': 'a'}})
                  if a.name == 'review_action')
    assert set(action.schema['properties']) == {'decision'}
    assert action.convert({'decision': decision}) == {'approval_id': 'a', 'decision': decision}


@pytest.mark.parametrize('stage', ['plan', 'compose'])
@pytest.mark.parametrize('preamble', [
    'I need to authenticate the user first. Let me ask for their identification details.',
    [{'type': 'thinking', 'thinking': 'private reasoning'},
     {'type': 'text', 'text': 'I should ask for an email.'}],
])
def test_sdk_reasoning_is_not_final_text(stage, preamble):
    p, models = provider(text='What is your email?')
    for model in models.values():
        model.responses[0] = AIMessage(content=[
            {'type': 'thinking', 'thinking': str(preamble)},
            {'type': 'text', 'text': 'What is your email?'}])
    result = asyncio.run(getattr(p, stage)(payload()))
    assert (result['response'] if stage == 'plan' else result) == 'What is your email?'
    assert sum(m.calls for m in models.values()) == 1


@pytest.mark.parametrize('stage', ['plan', 'compose'])
def test_native_text_terminal_needs_no_response_tool(stage):
    p, _ = provider(text='Hello!')
    result = asyncio.run(getattr(p, stage)(payload()))
    assert (result['response'] if stage == 'plan' else result) == 'Hello!'


@pytest.mark.parametrize('stage', ['plan', 'compose'])
@pytest.mark.parametrize('failure', ['invalid_args', 'retired_tool', 'refusal', 'max_tokens', 'reasoning_only'])
def test_valid_looking_text_never_masks_invalid_or_incomplete_protocol(stage, failure):
    p, models = provider(text='What is your email?')
    kwargs = {'content': 'What is your email?'}
    if failure == 'invalid_args':
        kwargs['invalid_tool_calls'] = [{'name': 'knowledge_search', 'args': '{bad', 'id': 'x', 'error': 'parse'}]
    elif failure == 'retired_tool':
        kwargs['tool_calls'] = [{'name': 'respond', 'args': {'response': 'Hello'}, 'id': 'x'}]
    elif failure == 'reasoning_only':
        kwargs['content'] = [{'type': 'thinking', 'thinking': 'Need an email.'}]
    else:
        kwargs['response_metadata'] = {'stop_reason': failure}
    for model in models.values():
        model.responses[0] = AIMessage(**kwargs)
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(getattr(p, stage)(payload()))
