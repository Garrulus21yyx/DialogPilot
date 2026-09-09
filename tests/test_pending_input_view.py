import json
import itertools
import pytest

from application.pending_input_view import pending_input_fields
from application.conversation_actions import planning_actions, action_proposal
from infrastructure.target_model_context import planning_context


def test_bindings_stay_private_across_field_collisions_and_partial_answers():
    for names in itertools.product(('color', 'color_1', 'size'), repeat=3):
        fields = [{'target_work_item_id': f'private-{i}', 'field_name': name, 'value_schema': 'string'}
                  for i, name in enumerate(names)]
        pending = {'interaction_id':'private-interaction', 'version':9, 'requested_fields':fields,
                   'objectives':[{'work_item_id':f'private-{i}', 'objective':f'Exchange item {i}'} for i in range(3)]}
        payload = {'message':'Blue, please.', 'supported_goals':[], 'pending_input':pending}
        payload['supplied_interaction_values'] = [{**{k:fields[1][k] for k in ('target_work_item_id','field_name')}, 'value':'Blue'}]
        actions = planning_actions(payload)
        projection = pending_input_fields(pending)
        assert len(projection) == 3
        _, messages = planning_context(payload)
        shown = json.dumps([m.content for m in messages]) + json.dumps(actions[0].tool())
        assert 'private-' not in shown
        assert 'value_schema' not in shown
        assert 'target_work_item_id' not in shown
        for key, (bound, _) in projection.items():
            result = action_proposal(actions, [{'id':'one', 'name':'supply_input',
                'args':{'values':{key:'Blue'}}}], '')
            assert result['input_values'] == [{'target_work_item_id':bound['target_work_item_id'],
                'field_name':bound['field_name'], 'value':'Blue'}]
        tail = messages[-1].content
        assert json.loads(tail[1]['text']) == {'current_request':'Blue, please.'}
        supplied = json.loads(tail[2]['text'])['supplied_interaction_values']
        assert len(supplied) == 1
        key = next(k for k, (item, _) in projection.items() if item == fields[1])
        assert supplied == {key:'Blue'}


def test_model_view_cannot_recreate_private_resume_bindings():
    with pytest.raises(ValueError, match='requires_authoritative_bindings'):
        planning_context({'message':'Blue', 'pending_input':{
            'requested_information':[{'field':'color','description':'Color for exchange'}]}})
