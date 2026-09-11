"""Historical business support survives publication without becoming action authority."""
import asyncio
import json
from dataclasses import replace

import pytest

from application.business_observation import BusinessObservation, capture_business_observations
from application.conversation_context import conversation_context_payload
from application.conversation_evidence import ConversationEvidence
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.publication import command_fingerprint
from application.response_assembly import ResponseAssembler
from core.identity import IdentityFactory
from infrastructure.postgres_conversation_evidence import PostgresConversationEvidence
from infrastructure.target_turn_context import TargetTurnContextLoader
from tests.test_knowledge_answer_boundary import Verifier
from tests.test_postgres_publication import publication_components, _final, _interaction
from tests.test_response_assembly import _board, _verified_order_result
from tests.test_target_turn_context import Memory, Tools, _identity, _state
from tests.test_work_control import _item


def observed_board():
    item = replace(_item('read', 1, work_item_id='lookup'), owner_agent='order_logistics',
                   requirement_ids=('order.current_state',))
    result = _verified_order_result(item.work_item_id)
    return replace(_board(result), work_items=(item,))


@pytest.mark.parametrize('size', [1, 1000, 30000])
@pytest.mark.parametrize('revision', [False, True])
def test_reply_has_one_immutable_history_copy_across_author_and_verifier(size, revision):
    import copy
    marker = 'UNIQUE_HISTORICAL_DETAIL'
    context = {'recent_messages': [], 'business_observations': [
        {'status':'HISTORICAL', 'publication_id':'p1',
         'observation':{'detail':marker + (' detail' * size)}}]}
    original = copy.deepcopy(context)
    requests = []
    class Author:
        async def compose(self, payload):
            requests.append(payload)
            return 'The previous lookup is available.'
    class ReviewingVerifier(Verifier):
        async def verify(self, *args, **kwargs):
            self.passed = not revision or bool(self.calls)
            return await super().verify(*args, **kwargs)
    verifier = ReviewingVerifier(True)
    result = asyncio.run(ResponseAssembler(Author(), knowledge_verifier=verifier).assemble(
        observed_board(), current_message='Explain the previous lookup', conversation_context=context))
    assert result.verified and context == original
    assert len(requests) == len(verifier.calls) == (2 if revision else 1)
    for request, (_, checked) in zip(requests, verifier.calls, strict=True):
        assert json.dumps(request).count(marker) == 1
        assert request['evidence']['user_context'] == original
        assert request['evidence'] == json.loads(checked['context'])
        assert json.loads(result.evidence_json) == request['evidence']


def test_capture_uses_original_governed_facts_not_response_prose():
    board = observed_board()
    captured = capture_business_observations(board)
    altered = replace(board, results=(replace(board.results[0], candidate_response='No order data exists'),))
    assert capture_business_observations(altered) == captured
    restored = BusinessObservation.model_validate_json(json.dumps(captured[0]))
    assert restored.facts == board.results[0].facts
    assert capture_business_observations(None) == ()
    assert capture_business_observations(replace(board, results=())) == ()


def test_historical_support_reaches_author_and_verifier_without_becoming_current_facts():
    observation = capture_business_observations(observed_board())[0]
    historical = {'publication_id': 'p1', 'status': 'HISTORICAL', 'observation': observation}
    class Reader:
        def load(self, invocation):
            return ConversationEvidence(business=(historical,))
    tools = Tools()
    obs, state = TurnObservations('Explain the result'), _state()
    context = asyncio.run(TargetTurnContextLoader(Memory(), tools, evidence_reader=Reader()).load(
        _identity(), obs, state, DeterministicResolver().resolve(obs, state)))
    payload = conversation_context_payload(context)
    assert payload['business_observations'] == [historical]
    from infrastructure.target_model_context import planning_context
    _, messages = planning_context({'message': obs.raw_text, 'conversation_context': payload})
    runtime = json.loads(messages[-1].content[0]['text'])['runtime_context']
    assert runtime['conversation_context']['business_observations'] == [historical]
    assert tools.calls == []
    class Author:
        async def compose(self, request):
            assert 'conversation_context' not in request
            assert request['evidence']['user_context']['business_observations'] == [historical]
            assert request['evidence']['facts'] == []
            return 'The previous lookup reported shipped.'
    verifier = Verifier(True)
    assembler = ResponseAssembler(Author(), knowledge_verifier=verifier)
    authored = asyncio.run(assembler._assemble_candidate(_board(), current_message=obs.raw_text,
        conversation_context=payload, repair_feedback={'reason': 'verify historical result'}))
    assert authored.text == 'The previous lookup reported shipped.'
    response = asyncio.run(assembler.assemble(
        None, current_message=obs.raw_text, conversation_context=payload,
        response_candidate='The previous lookup reported shipped.'))
    assert response.verified
    evidence = json.loads(verifier.calls[0][1]['context'])
    assert evidence['facts'] == [] and evidence['pending_actions'] == []
    assert evidence['user_context']['business_observations'] == [historical]


def test_domain_receives_historical_observation_separately_from_verified_facts():
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from tests.test_target_framework_agent import (
        ScriptedToolModel, _context, _manager, InMemoryStore, build_default_capability_registry,
    )
    observation = {'status': 'HISTORICAL', 'publication_id': 'p1',
                   'observation': capture_business_observations(observed_board())[0]}
    model = ScriptedToolModel(responses=[])
    agent = TargetFrameworkAgent(model, _manager([]), review_model=model,
        review_available_tokens=14200, result_store=InMemoryStore(),
        registry=build_default_capability_registry('tenant-a'), system_prompt='product')
    context = _context(business_observations=(observation,))
    context = replace(context, evidence_refs=tuple(
        fact['source_ref'] for fact in observation['observation']['facts']))
    blocks = agent._build_prompt(context)
    runtime = json.loads(blocks[1]['text'])['runtime_context']
    assert runtime['business_observations'] == [observation]
    assert runtime['verified_facts'] == []
    assert runtime['pending_approval'] is None


@pytest.mark.parametrize('kind', ['final', 'FIELDS', 'APPROVAL'])
@pytest.mark.parametrize('verifier_status', ['passed', 'failed'])
def test_publication_roundtrip_preserves_private_business_observations(
        publication_components, kind, verifier_status):
    pool, identity, service, _ = publication_components
    observation = capture_business_observations(observed_board())[0]
    command = (_final(identity) if kind == 'final' else
               replace(_interaction(identity, pool), resume_schema={'interaction_kind': kind}))
    if kind == 'FIELDS':
        command = replace(command, signal_id='fields-signal', signal_version=3)
    if kind == 'final':
        command = replace(command, verifier_status=verifier_status)
    changed = replace(command, business_observations=(observation, observation))
    assert command_fingerprint(command) != command_fingerprint(changed)
    publish = service.select_final_response if kind == 'final' else service.publish_interaction_request
    selected = publish(changed)
    assert publish(changed).record.publication_id == selected.record.publication_id
    with pool.transaction() as connection:
        public, private = connection.execute(
            'SELECT payload, verification FROM dialogpilot_app.response_deliveries WHERE publication_id=%s',
            (selected.record.publication_id,)).fetchone()
    assert 'business_observations' not in json.dumps(public)
    assert private['business_observations'] == [observation, observation]
    following = IdentityFactory(lambda: 'unused').create_invocation(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), request_id='following')
    # Recreate the reader, including the tool-only environment without a knowledge validator.
    reader = PostgresConversationEvidence(pool)
    assert reader.load(identity) == ConversationEvidence()
    evidence = reader.load(following)
    assert evidence.knowledge == () and len(evidence.business) == 1
    assert evidence.business[0]['status'] == 'HISTORICAL'
    assert evidence.business[0]['observation'] == observation
    for field in ('tenant_id', 'user_id', 'conversation_id'):
        assert reader.load(replace(following, **{field: 'other'})) == ConversationEvidence()


def test_invalid_observation_has_no_fact_payload_and_does_not_hide_valid_sibling():
    observation = capture_business_observations(observed_board())[0]
    result = PostgresConversationEvidence._business([
        ('p1', {'business_observations': [{'schema_version': 'future'}, observation]})])
    assert result[0] == {'publication_id': 'p1', 'status': 'INVALID',
                         'reason_code': 'BUSINESS_OBSERVATION_INVALID'}
    assert result[1]['observation'] == observation


def test_original_reference_is_independent_of_json_key_order():
    observation = capture_business_observations(observed_board())[0]
    reversed_fields = dict(reversed(list(observation.items())))
    reversed_fields['coverage'] = dict(reversed(list(observation['coverage'].items())))
    assert BusinessObservation.model_validate(observation).observation_id == BusinessObservation.model_validate(reversed_fields).observation_id


def test_original_reader_is_scoped_and_does_not_depend_on_recent_window(publication_components):
    from infrastructure.postgres_conversation_evidence import BusinessObservationUnavailable
    pool, identity, service, _ = publication_components
    original = BusinessObservation.model_validate(capture_business_observations(observed_board())[0])
    command = replace(_final(identity), business_observations=(original.model_dump(mode='json'),))
    publication = service.select_final_response(command).record.publication_id
    reader = PostgresConversationEvidence(pool, recent_limit=0)
    assert reader.load(identity) == ConversationEvidence()
    restored = reader.read_business(identity, publication_id=publication, observation_id=original.observation_id)
    assert restored == original
    for field in ('tenant_id', 'user_id', 'conversation_id'):
        with pytest.raises(BusinessObservationUnavailable):
            reader.read_business(replace(identity, **{field: 'other'}),
                publication_id=publication, observation_id=original.observation_id)
    for publication_id, observation_id in ((publication, 'wrong'), ('missing', original.observation_id)):
        with pytest.raises(BusinessObservationUnavailable):
            reader.read_business(identity, publication_id=publication_id, observation_id=observation_id)


@pytest.mark.parametrize('dependent', [False, True])
def test_historical_coverage_preserves_conflict_impact_and_independent_success(dependent):
    board = observed_board()
    first = board.work_items[0]
    second = replace(first, work_item_id='second', dependencies=(first.work_item_id,) if dependent else ())
    other = _verified_order_result('second', order_id='DP9876')
    conflict = f'{board.results[0].facts[0].subject_ref}:order.current_state'
    board = replace(board, work_items=(first, second), results=(*board.results, other), conflict_keys=(conflict,))
    observations = capture_business_observations(board)
    assert observations[0]['coverage']['delivery_reason'] == 'CONFLICT_AFFECTED'
    assert observations[1]['coverage']['delivery_reason'] == ('CONFLICT_AFFECTED' if dependent else 'DELIVERABLE')
    assert observations[1]['status'] == 'SUCCEEDED'


@pytest.mark.parametrize('effect', ['NOT_COMMITTED', 'UNCONFIRMED'])
def test_write_recovery_without_facts_survives_with_its_exact_operation(effect):
    from application.agent_result import AgentResultStatus
    from tests.test_write_workflow import _item as write_item
    from tests.test_response_assembly import _result
    item = write_item('operation-1')
    feedback = {'stage': 'write_recovery', 'operation_key': item.operation_key,
        'status': 'MANUAL_REVIEW', 'reason': 'REJECTED', 'ticket_id': None,
        'recovery_attempts': 1, 'business_outcome': effect, 'last_outcome': None,
        'outcome_scope': 'OPERATION', 'detail': 'Read the operation status', 'source_ref': 'ledger:1'}
    result = replace(_result(item.work_item_id, item.owner_agent, AgentResultStatus.BLOCKED),
                     execution_feedback=(feedback,))
    board = replace(_board(result), work_items=(item,))
    observation, = capture_business_observations(board)
    assert observation['write_recovery'] == [feedback]
    assert observation['facts'] == observation['receipts'] == []
    assert BusinessObservation.model_validate_json(json.dumps(observation)).write_recovery[0].business_outcome == effect
    assert capture_business_observations(replace(board,
        results=(replace(result, execution_feedback=({**feedback, 'operation_key': 'other'},)),))) == ()


@pytest.mark.parametrize('matching', [False, True])
def test_receipt_action_terms_use_the_same_operation_join_as_current_response(matching):
    from application.agent_result import ReceiptRef
    from application.response_assembly import _response_context
    from tests.test_write_workflow import _item as write_item
    from tests.test_response_assembly import _result
    item = write_item('operation-1')
    receipt = ReceiptRef('receipt', 'v1', 'operation-1' if matching else 'operation-2',
                         'COMMITTED', 'refund.request_action')
    result = _result(item.work_item_id, item.owner_agent, receipts=(receipt,))
    board = replace(_board(result), work_items=(item,))
    observation, = capture_business_observations(board)
    assert observation['receipts'] == _response_context(board)['receipts']
    assert (observation['receipts'][0]['action'] is not None) == matching


@pytest.mark.parametrize('outcome', ['COMMITTED', 'REJECTED', 'OUTCOME_UNKNOWN'])
def test_actual_write_runtime_result_survives_publication_and_recreated_loader(publication_components, outcome):
    from application.capability_registry import WriteRecoveryPolicy
    from application.write_workflow import (
        GovernedWriteRuntime, InMemoryOperationLedger, WriteToolOutcome, WriteOutcomeStatus,
    )
    from tests.test_write_workflow import _item as write_item, _context, _grant, ToolPort, Reconciler
    pool, identity, service, _ = publication_components
    item = write_item()
    item = replace(item, reconciliation=replace(item.reconciliation,
        recovery=WriteRecoveryPolicy(max_attempts=1, delay_seconds=0)))
    write = WriteToolOutcome(WriteOutcomeStatus(outcome),
        receipt_id='receipt-1' if outcome == 'COMMITTED' else '',
        receipt_schema_version='v1' if outcome == 'COMMITTED' else '',
        reason_code='SOURCE_RESULT', detail='Original business result', source_ref='source:1')
    tool, reconciler = ToolPort([write]), Reconciler([write])
    time = [0.0]
    async def advance(seconds):
        time[0] += seconds
    runtime = GovernedWriteRuntime(ledger=InMemoryOperationLedger(), tool_port=tool,
        reconciliation_port=reconciler, approval_grants={item.approval_binding: _grant()},
        clock=lambda: time[0], sleep=advance)
    result = asyncio.run(runtime(_context(item)))
    board = replace(_board(result), work_items=(item,))
    captured = capture_business_observations(board)
    assert len(captured) == 1
    # Failed wording must not erase the output produced by the actual write runtime.
    command = replace(_final(identity), verifier_status='failed', business_observations=captured)
    service.select_final_response(command)
    following = IdentityFactory(lambda: 'unused').create_invocation(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), request_id='after-write')
    obs, state = TurnObservations('What happened?'), _state()
    context = asyncio.run(TargetTurnContextLoader(Memory(), Tools(),
        evidence_reader=PostgresConversationEvidence(pool)).load(following, obs, state,
            DeterministicResolver().resolve(obs, state)))
    stored, = context.business_observations
    assert stored['observation'] == captured[0]
    observation = stored['observation']
    if outcome == 'COMMITTED':
        assert observation['receipts'][0]['operation_key'] == item.operation_key
        assert observation['receipts'][0]['action']['arguments'] == {a.name: a.value for a in item.arguments}
    else:
        recovery, = observation['write_recovery']
        assert recovery['business_outcome'] == ('NOT_COMMITTED' if outcome == 'REJECTED' else 'UNCONFIRMED')
        assert recovery['detail'] == write.detail and recovery['source_ref'] == write.source_ref
        assert observation['receipts'] == []
    assert len(tool.calls) == 1
    assert len(reconciler.calls) == (1 if outcome == 'OUTCOME_UNKNOWN' else 0)


@pytest.mark.parametrize('verified', [False, True])
@pytest.mark.parametrize('waiting', [False, True])
def test_actual_chat_publication_captures_read_even_when_reply_is_rejected(publication_components, verified, waiting):
    from application.chat_contracts import ChatCommand
    from application.default_capability_registry import build_default_capability_registry
    from application.orchestration_runtime import OrchestrationRuntime
    from application.target_chat_application import TargetChatApplication
    from application.target_conversation_manager import TargetConversationManager
    from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
    from infrastructure.postgres_target_runtime import PostgresConversationStateStore
    from infrastructure.target_chat_adapters import PostgresTargetPublication
    from tests.test_target_chat_cutover import _Admission
    from tests.test_turn_runtime import _Executor, _OrderUnderstanding
    pool, identity, _, _ = publication_components
    from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
    class Executor(_Executor):
        async def __call__(self, context):
            if context.work_item.work_item_id.endswith(':waiting-read'):
                return AgentResult(context.work_item.work_item_id, context.work_item.owner_agent,
                    AgentResultStatus.NEEDS_USER_INPUT, 'INPUT_REQUIRED', 'test-v1',
                    missing_inputs=(MissingInputSpec('reply', context.work_item.work_item_id,
                        'INPUT_REQUIRED', 'string', 'Which item?'),))
            return await super().__call__(context)
    class Understanding(_OrderUnderstanding):
        async def __call__(self, *args, **kwargs):
            proposal = await super().__call__(*args, **kwargs)
            if waiting:
                proposal = replace(proposal, commands=(*proposal.commands,
                    replace(proposal.commands[0], command_id='waiting-read')))
            return proposal
    executor = Executor()
    verifier = Verifier(verified)
    class Composer:
        async def compose(self, payload):
            assert payload['evidence']['facts']
            return 'Order DP1234 has shipped.'
    app = TargetChatApplication(manager=TargetConversationManager(
        state_store=PostgresConversationStateStore(pool),
        registry=build_default_capability_registry(str(identity.tenant_id)),
        understanding=Understanding(),
        orchestration=OrchestrationRuntime(direct_executor=executor, domain_workers={})),
        admission=_Admission(),
        publication=PostgresTargetPublication(PostgresResponseDeliveryService(pool, resume_binding_secret='test')),
        bundle_version='test', response_assembler=ResponseAssembler(Composer(), knowledge_verifier=verifier))
    command = ChatCommand('Query order DP1234', str(identity.user_id), str(identity.tenant_id),
                          str(identity.conversation_id), str(identity.request_id))
    published = asyncio.run(app.execute_admitted(command, identity))
    publication_id = getattr(published, 'response_id', None) or getattr(published, 'interaction_publication_id', None)
    assert publication_id
    assert verifier.calls
    if not (waiting and verified):
        assert published.response['verified'] is verified
    else:
        assert published.kind == 'FIELDS'
    following = IdentityFactory(lambda: 'unused').create_invocation(
        tenant_id=str(identity.tenant_id), user_id=str(identity.user_id),
        conversation_id=str(identity.conversation_id), request_id='after-chat')
    evidence = PostgresConversationEvidence(pool).load(following)
    assert evidence.business and evidence.business[0]['observation']['facts']
    assert executor.calls == 1
    replay = app.completed(identity)
    assert (getattr(replay, 'response_id', None) or getattr(replay, 'interaction_publication_id', None)) == publication_id
    assert executor.calls == 1
