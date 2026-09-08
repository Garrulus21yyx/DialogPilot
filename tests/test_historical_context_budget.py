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
from application.deterministic_resolution import ResolutionKind


def historical(size=360000):
    data = capture_business_observations(observed_board())[0]
    data['facts'][0]['value_json'] = json.dumps({'status':'shipped', 'detail':'x' * size},
                                             sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    original = BusinessObservation.model_validate(data)
    return original, {'status':'HISTORICAL', 'publication_id':'p1',
                      'observation_id':original.observation_id, 'observation':data}


@pytest.mark.parametrize('kind', list(ResolutionKind))
def test_host_projection_precedes_every_resolution_branch_without_model_or_tool_call(kind):
    from types import SimpleNamespace
    from application.conversation_evidence import ConversationEvidence
    from application.deterministic_resolution import TurnObservations
    from infrastructure.target_turn_context import TargetTurnContextLoader
    from tests.test_target_turn_context import Memory, Tools, _identity, _state
    original, entry = historical()
    class Reader:
        def load(self, invocation):
            return ConversationEvidence(business=(entry,))
    tools = Tools()
    context = asyncio.run(TargetTurnContextLoader(Memory(), tools, evidence_reader=Reader(),
        historical_context_budget=ContextBudgetManager()).load(
            _identity(), TurnObservations('Continue'), _state(), SimpleNamespace(kind=kind)))
    assert context.business_observations[0]['observation']['facts'][0]['value_reference']
    assert tools.calls == [] and entry['observation']['facts'][0]['value_json'] == original.facts[0].value_json


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


@pytest.mark.parametrize('backend', ['memory', 'postgres'])
def test_selected_history_survives_manager_observation_checkpoint_and_reply(backend, request):
    from contextlib import asynccontextmanager
    database_url = request.getfixturevalue('postgres_database_url') if backend == 'postgres' else None
    from application.conversation_agent import ConversationAgent
    from application.conversation_actions import planning_actions, action_proposal
    from application.conversation_state import InMemoryConversationStateStore
    from application.deterministic_resolution import TurnObservations
    from application.orchestration_runtime import OrchestrationRuntime
    from application.response_assembly import ResponseAssembler
    from application.target_conversation_manager import TargetConversationManager, TargetTurnContext
    from application.turn_runtime import TurnRuntime
    from infrastructure.conversation_tool_catalog import ConversationToolCatalog
    from infrastructure.target_tool_execution import TargetToolExecutor
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    from langgraph.checkpoint.memory import InMemorySaver
    from tests.test_conversation_actions import calls
    from tests.test_conversation_observation_tool import setup_reader
    from tests.test_knowledge_answer_boundary import Verifier
    from tests.test_turn_runtime import _identity

    original, entry = historical()
    _, reader, tools, registry = setup_reader()
    reads = []
    def read(invocation, **kwargs):
        reads.append(kwargs)
        return original
    reader.read_business = read
    model_inputs = []
    class Provider:
        async def plan(self, payload):
            model_inputs.append(copy.deepcopy(payload))
            context = payload['conversation_context']
            assert context['business_observations'][0]['observation']['facts'][0]['value_reference']
            if context.get('observed_execution'):
                return {'status':'respond', 'response':'The previous lookup reported shipped.'}
            actions = planning_actions(payload)
            tool = next(action for action in actions if action.kind == 'atomic_read')
            return action_proposal(actions, calls((tool.name, {
                'publication_id':'p1','observation_id':original.observation_id,
                'pointer':'/facts/0/value/status'})), '')
    agent = ConversationAgent(Provider(), tool_catalog=ConversationToolCatalog(tools))
    class Understanding:
        async def __call__(self, *args):
            return await agent.plan(*args)
    class Context:
        async def load(self, *args):
            return TargetTurnContext(business_observations=(entry,))
    memory_saver = InMemorySaver(serde=target_checkpoint_serializer())
    @asynccontextmanager
    async def checkpoint():
        if database_url:
            from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
            async with AsyncPostgresCheckpointOwner(database_url, setup=True) as saver:
                yield saver
        else:
            yield memory_saver
    async def run():
        verifier = Verifier(True)
        store = InMemoryConversationStateStore()
        def runtime(saver):
            manager = TargetConversationManager(state_store=store,
                registry=registry, understanding=Understanding(), context_provider=Context(),
                orchestration=OrchestrationRuntime(direct_executor=TargetToolExecutor(tools, registry=registry),
                                                   domain_workers={}, checkpointer=saver))
            return TurnRuntime(manager, ResponseAssembler(knowledge_verifier=verifier), checkpointer=saver)
        identity, message = _identity(), TurnObservations('Explain the previous lookup')
        async with checkpoint() as saver:
            result = await runtime(saver).execute(identity, message)
        assert result.assembled.verified and len(reads) == 1 and len(model_inputs) == 2
        evidence = json.loads(result.assembled.evidence_json)
        assert evidence['user_context']['business_observations'][0]['observation']['facts'][0]['value_reference']
        assert 'shipped' in json.dumps(evidence['facts'])
        assert 'x' * 30000 not in json.dumps(evidence)
        assert json.loads(verifier.calls[0][1]['context']) == evidence
        async with checkpoint() as saver:
            replay = await runtime(saver).execute(identity, message)
        assert replay.assembled.evidence_json == result.assembled.evidence_json and len(reads) == 1
        assert entry['observation']['facts'][0]['value_json'] == original.facts[0].value_json
    asyncio.run(run())
