import asyncio
import copy
import json

import pytest
from jsonpointer import resolve_pointer

from application.business_observation import BusinessObservation, capture_business_observations
from application.context_budget import ContextBudgetManager, ModelContextBudgetExceeded
from application.historical_context_budget import fit_historical_payload
from infrastructure.conversation_observation_tool import observation_document
from tests.test_business_observation_continuity import observed_board


def historical(size=360000):
    data = capture_business_observations(observed_board())[0]
    data['facts'][0]['value_json'] = json.dumps({'status':'shipped', 'detail':'x' * size},
                                             sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    original = BusinessObservation.model_validate(data)
    return original, {'status':'HISTORICAL', 'publication_id':'p1',
                      'observation_id':original.observation_id, 'observation':data}


@pytest.mark.parametrize('size', [0, 1000, 360000])
def test_projection_is_bounded_and_preserves_original_metadata(size):
    original, entry = historical(size)
    payload = {'history':[entry], 'message':'Explain the previous result',
               'recent':[{'content':'Only query, do not submit.'}]}
    untouched = copy.deepcopy(payload)
    budget = ContextBudgetManager()
    projected = fit_historical_payload(budget, payload, observation_path=('history',), trim_oldest_paths=('recent',))
    assert payload == untouched and projected.report.final_tokens <= budget.available_tokens
    assert projected.payload['recent'] == payload['recent']
    assert projected == fit_historical_payload(budget, payload, observation_path=('history',), trim_oldest_paths=('recent',))
    observation = projected.payload['history'][0]['observation']
    fact = observation['facts'][0]
    for key, value in original.facts[0].__dict__.items():
        if key != 'value_json':
            assert fact[key] == entry['observation']['facts'][0][key]
    assert observation['coverage'] == original.coverage
    if size == 360000:
        assert 'value_json' not in fact
        reference = fact['value_reference']
        assert reference['status'] == 'NOT_EXPANDED'
        assert resolve_pointer(observation_document(original), reference['arguments']['pointer']) == json.loads(original.facts[0].value_json)
        assert projected.report.externalized_items
    else:
        assert projected.payload == payload and not projected.report.externalized_items


def test_missing_original_reference_and_oversized_metadata_are_not_silently_dropped():
    _, entry = historical()
    entry.pop('observation_id')
    with pytest.raises(ModelContextBudgetExceeded):
        fit_historical_payload(ContextBudgetManager(), {'history':[entry]}, observation_path=('history',))
    _, entry = historical(1)
    entry['observation']['coverage']['detail'] = 'x' * 360000
    with pytest.raises(ModelContextBudgetExceeded):
        fit_historical_payload(ContextBudgetManager(), {'history':[entry]}, observation_path=('history',))


@pytest.mark.parametrize('kind', ['receipt', 'recovery'])
def test_action_and_recovery_bodies_externalize_without_changing_effect_knowledge(kind):
    _, entry = historical(1)
    data = entry['observation']
    if kind == 'receipt':
        data['receipts'] = [{'receipt_id':'r1', 'schema_version':'v1', 'operation_key':'op1',
            'effect_status':'COMMITTED', 'requirement_id':'write.request',
            'action':{'target_entity_ref':'order:1','arguments':{'details':'x' * 360000}}}]
        path, body = 'receipts', 'arguments'
    else:
        data['write_recovery'] = [{'stage':'write_recovery','operation_key':'op1',
            'status':'MANUAL_REVIEW','reason':'UNKNOWN','ticket_id':None,'recovery_attempts':1,
            'business_outcome':'UNCONFIRMED','last_outcome':'UNKNOWN','outcome_scope':'ORIGINAL',
            'detail':'x' * 360000,'source_ref':'operation:op1'}]
        path, body = 'write_recovery', 'detail'
    original = BusinessObservation.model_validate(data)
    entry['observation_id'] = original.observation_id
    result = fit_historical_payload(ContextBudgetManager(), {'history':[entry]}, observation_path=('history',))
    projected = result.payload['history'][0]['observation'][path][0]
    owner = projected['action'] if kind == 'receipt' else projected
    reference = owner[body + '_reference']['arguments']
    expected = data[path][0]['action'][body] if kind == 'receipt' else data[path][0][body]
    assert resolve_pointer(observation_document(original), reference['pointer']) == expected
    assert projected['operation_key'] == 'op1'
    if kind == 'receipt':
        assert projected['effect_status'] == 'COMMITTED' and owner['target_entity_ref'] == 'order:1'
    else:
        assert projected['business_outcome'] == 'UNCONFIRMED' and projected['source_ref'] == 'operation:op1'
def test_reply_does_not_externalize_evidence_into_unreadable_references():
    from application.response_assembly import ResponseAssembler
    from tests.test_knowledge_answer_boundary import Verifier
    _, entry = historical()
    requests = []
    class Author:
        async def compose(self, payload):
            requests.append(copy.deepcopy(payload))
            return 'The original lookup reported shipped.'
    class Review(Verifier):
        async def verify(self, *args, **kwargs):
            self.passed = bool(self.calls)
            return await super().verify(*args, **kwargs)
    verifier = Review(False)
    result = asyncio.run(ResponseAssembler(Author(), knowledge_verifier=verifier).assemble(
        observed_board(), current_message='Explain the previous result',
        conversation_context={'business_observations':[entry]}))
    assert result.verified and len(requests) == len(verifier.calls) == 2
    for request, (_, checked) in zip(requests, verifier.calls, strict=True):
        assert request['evidence'] == json.loads(checked['context']) == json.loads(result.evidence_json)
        fact = request['evidence']['user_context']['business_observations'][0]['observation']['facts'][0]
        assert 'value_reference' not in fact and fact['value_json'] == entry['observation']['facts'][0]['value_json']
        assert request['evidence']['facts']  # Current evidence is not externalized as history.


def test_main_planner_can_select_the_historical_read_after_budgeting():
    from application.conversation_agent import ConversationAgent
    from application.conversation_actions import planning_actions, action_proposal
    from application.deterministic_resolution import DeterministicResolver, TurnObservations
    from application.target_conversation_manager import TargetTurnContext
    from application.turn_planning import ProposalDisposition
    from infrastructure.conversation_tool_catalog import ConversationToolCatalog
    from tests.test_conversation_actions import calls
    from tests.test_conversation_agent import _state
    from tests.test_conversation_observation_tool import setup_reader
    _, _, manager, registry = setup_reader()
    _, entry = historical()
    class Provider:
        async def plan(self, payload):
            ref = payload['conversation_context']['business_observations'][0]['observation']['facts'][0]['value_reference']
            actions = planning_actions(payload)
            read = next(action for action in actions if action.kind == 'atomic_read')
            return action_proposal(actions, calls((read.name, ref['arguments'])), '')
    observations, state = TurnObservations('Explain the previous lookup'), _state()
    result = asyncio.run(ConversationAgent(Provider(), tool_catalog=ConversationToolCatalog(manager)).plan(
        observations, state, DeterministicResolver().resolve(observations, state), registry,
        TargetTurnContext(business_observations=(entry,))))
    assert result.disposition is ProposalDisposition.RESOLVED
    assert len(result.commands) == 1


@pytest.mark.parametrize(('size', 'overhead'), [(360000, 1000), (30000, 8000)])
def test_domain_prompt_fits_large_history_with_its_read_capability(size, overhead):
    from dataclasses import replace
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from tests.test_target_framework_agent import ScriptedToolModel, _context, _manager, InMemoryStore
    from application.default_capability_registry import build_default_capability_registry
    _, entry = historical(size)
    model = ScriptedToolModel(responses=[])
    agent = TargetFrameworkAgent(model, _manager([]), review_model=model, review_available_tokens=14200,
        result_store=InMemoryStore(), registry=build_default_capability_registry('tenant-a'), system_prompt='Assist')
    context = _context(business_observations=(entry,))
    context = replace(context, work_item=replace(context.work_item,
        allowed_tools=(*context.work_item.allowed_tools, 'read_conversation_observation')))
    prompt = asyncio.run(agent._prepare_prompt(context, overhead_tokens=overhead))
    runtime = json.loads(prompt[1]['text'])['runtime_context']
    assert runtime['business_observations'][0]['observation']['facts'][0]['value_reference']
    assert not runtime['verified_facts'] and model.calls == 0
