from scripts.replay_domain_assessment import remove_actor_text


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
