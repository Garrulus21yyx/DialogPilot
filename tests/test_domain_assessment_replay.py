from scripts.replay_domain_assessment import remove_actor_text
from scripts.replay_domain_assessment import remove_runtime_advice
from scripts.replay_domain_assessment import ASSESSMENT_BASIS_SCHEMA, SCHEMA


def test_basis_probe_keeps_original_decision_fields_and_closed_schema():
    assert all(ASSESSMENT_BASIS_SCHEMA['properties'][key] == value for key, value in SCHEMA['properties'].items())
    assert set(ASSESSMENT_BASIS_SCHEMA['required']) == set(SCHEMA['required']) | {'policy_basis', 'goal_impact'}
    assert ASSESSMENT_BASIS_SCHEMA['additionalProperties'] is False


def test_runtime_ablation_preserves_nonexecution_and_all_other_messages():
    payload = {'working_context': [
        {'role': 'tool', 'content': 'No calls in this batch were executed. Only one proposal.', 'tool_call_id': 'c'},
        {'role': 'tool', 'content': 'Object is ready', 'tool_call_id': 'read'},
        {'role': 'ai', 'content': 'Prepare A', 'tool_calls': [{'name': 'A'}]},
    ]}
    result = remove_runtime_advice(payload)
    assert result['working_context'][0] == {**payload['working_context'][0], 'content': 'No calls in this batch were executed.'}
    assert result['working_context'][1:] == payload['working_context'][1:]
    assert payload['working_context'][0]['content'].endswith('Only one proposal.')


def test_actor_ablation_preserves_facts_tools_order_and_original_payload():
    payload = {'policy': 'unchanged', 'working_context': [
        {'role': 'human', 'content': 'observed facts'},
        {'role': 'ai', 'content': [{'type': 'text', 'text': 'independent'},
                                  {'type': 'tool_use', 'id': 'c', 'input': {'id': 'X'}}],
         'tool_calls': [{'id': 'c', 'name': 'prepare', 'args': {'id': 'X'}}]},
        {'role': 'tool', 'content': 'No calls executed', 'tool_call_id': 'c'},
        {'role': 'ai', 'content': 'prepare first', 'tool_calls': []},
    ]}
    result = remove_actor_text(payload)
    assert result['policy'] == payload['policy']
    assert len(result['working_context']) == 4
    assert result['working_context'][0] == payload['working_context'][0]
    assert result['working_context'][2] == payload['working_context'][2]
    assert result['working_context'][1]['tool_calls'] == payload['working_context'][1]['tool_calls']
    assert result['working_context'][1]['content'] == payload['working_context'][1]['content'][1:]
    assert result['working_context'][3]['content'] == ''
    assert payload['working_context'][1]['content'][0]['type'] == 'text'
