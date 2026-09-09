"""Native authorship, one evidence snapshot and runtime-owned interaction."""
import asyncio
import hashlib
import json
from dataclasses import make_dataclass, replace
from types import SimpleNamespace as NS

import pytest
from langchain_core.messages import AIMessage
from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
from application.conversation_state import PendingApprovalState
from application.response_assembly import ResponseAssembler, _APPROVAL_DESCRIPTION_REQUIREMENT
from core.model_policy import ModelProfile, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from tests.framework_structured_stub import StructuredStub
from tests.test_response_assembly import _board, _Composer, _verified_order_result
from tests.test_approval_conversation import ApprovalVerifier


@pytest.mark.parametrize('content', ['Which color?', '已准备好方案，是否继续？',
    [{'type': 'thinking', 'thinking': 'private analysis'}, {'type': 'text', 'text': 'Visible answer.'}]])
def test_sdk_public_answer_uses_one_flat_field_not_working_text(content):
    model = StructuredStub(responses=[AIMessage(content=content, tool_calls=[
        {'name': 'respond', 'args': {'response': 'Which color?'}, 'id': 'public'}])])
    provider = AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS: model},
        model_profile=ModelProfile('test'), synthesis_profile=ModelProfile('test'))
    result = asyncio.run(provider.compose({'evidence': {}, 'current_message': 'Help'}))
    assert result == 'Which color?'
    assert model.bound_tool_names == ['respond']


@pytest.mark.parametrize('message', [AIMessage(content=''),
    AIMessage(content='Partial', response_metadata={'stop_reason': 'max_tokens'}),
    AIMessage(content='No', response_metadata={'stop_reason': 'refusal'}),
    AIMessage(content='', tool_calls=[{'name': 'submit_composed_response', 'args': {}, 'id': '1'}])])
def test_invalid_native_completion_remains_an_explicit_provider_failure(message):
    from application.conversation_agent import ConversationProviderOutputError
    provider = AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS: StructuredStub(responses=[message])},
        model_profile=ModelProfile('test'), synthesis_profile=ModelProfile('test'))
    with pytest.raises(ConversationProviderOutputError):
        asyncio.run(provider.compose({'evidence': {}}))


@pytest.mark.parametrize('has_input', [False, True])
@pytest.mark.parametrize('terms_complete', [False, True])
def test_selected_approval_presentation_is_independent_of_other_input(has_input, terms_complete):
    pending = PendingApprovalState('approval', 1, 'flow', 'action', 'order.cancel:v1', 'operation',
        'order:DP1234', '1', '2099-01-01T00:00:00+00:00')
    specs = (MissingInputSpec('reply', 'w', 'MISSING', 'string', 'Which option?'),) if has_input else ()
    board = _board(AgentResult('w', 'retail', AgentResultStatus.NEEDS_USER_INPUT,
        'MISSING', 'test', missing_inputs=specs)) if has_input else _board(_verified_order_result())
    composer = _Composer('Cancel DP1234; not executed. Shall I proceed?' + (' Which option for the other task?' if has_input else ''))
    verifier = ApprovalVerifier('ANSWERED' if terms_complete else 'MISSING')
    answer = asyncio.run(ResponseAssembler(composer, knowledge_verifier=verifier).assemble(
        board, current_message='Continue', pending_approval=pending, requested_inputs=specs))
    assert answer.verified is terms_complete
    evidence = json.loads(verifier.calls[0][1]['context'])
    assert evidence['pending_actions'][0]['effect_status'] == 'NOT_EXECUTED'
    assert _APPROVAL_DESCRIPTION_REQUIREMENT in composer.calls[0]['response_requirements']
    assert bool(answer.approval_operation_key) is terms_complete


def test_turn_runtime_does_not_drop_persisted_approval_while_asking_for_input():
    from application.turn_runtime import TurnRuntime
    from application.target_conversation_manager import TargetTurnContext
    from tests.test_knowledge_answer_boundary import Verifier
    pending = PendingApprovalState('approval', 1, 'flow', 'action', 'order.cancel:v1', 'operation',
        'order:DP1234', '1', '2099-01-01T00:00:00+00:00')
    spec = MissingInputSpec('reply', 'w', 'MISSING', 'string', 'Which option?')
    board = _board(AgentResult('w', 'retail', AgentResultStatus.NEEDS_USER_INPUT, 'MISSING', 'test', missing_inputs=(spec,)))
    composer = _Composer('Which option?')
    runtime = TurnRuntime(NS(), ResponseAssembler(composer, knowledge_verifier=Verifier(True)))
    managed = NS(board=board, interaction_questions=(spec,), diagnostics=(), request_completed=False,
        state_before=NS(pending_interaction=None, pending_approval=pending),
        state_after=NS(pending_interaction=NS(interaction_id='input', version=1, requested_fields=(spec,), suspended_work_items=()), pending_approval=pending),
        plan=NS(response_text=None, route=NS(reason_code='CONTINUE', missing_inputs=())))
    result = asyncio.run(runtime._assemble_response({'managed': managed, 'observations': NS(raw_text='Wait'),
        'presentation_state': managed.state_before,
        'prepared': NS(context=TargetTurnContext())}))
    # The existing bound-question fast path needs neither rewrite nor re-approval.
    assert not composer.calls
    assert managed.state_after.pending_approval is pending
    assert result['assembled'].interaction_ready
    assert result['assembled'].approval_operation_key == ''


def test_old_segmented_checkpoint_is_typed_rejection_not_silent_none():
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer, TargetCheckpointContractError
    old_type = make_dataclass('AssembledResponse', [('used_claim_ids', tuple)],
        namespace={'__module__': 'application.response_assembly'})
    old_type.__module__ = 'application.response_assembly'
    codec = target_checkpoint_serializer()
    encoded = codec.dumps_typed({'assembled': old_type(('old',))})
    with pytest.raises(TargetCheckpointContractError, match='reply regeneration'):
        codec.loads_typed(encoded)


def test_current_checkpoint_retains_exact_evidence_and_verified_text():
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    from tests.test_knowledge_answer_boundary import Verifier
    response = asyncio.run(ResponseAssembler(_Composer('已发货。'), knowledge_verifier=Verifier(True)).assemble(
        _board(_verified_order_result()), current_message='查询'))
    codec = target_checkpoint_serializer()
    restored = codec.loads_typed(codec.dumps_typed({'assembled': response}))['assembled']
    assert restored == response
    assert restored.evidence_sha256 == hashlib.sha256(restored.evidence_json.encode()).hexdigest()


@pytest.mark.parametrize('field,value', [
    ('producer_id', 'another-producer'), ('producer_version', 'another-version'),
    ('source_ref', 'another-source'), ('subject_ref', 'order:other'),
    ('value_json', '{"status":"cancelled"}'),
    ('observation_started_at', 'observed'), ('valid_until', 'observed'),
])
def test_fact_authority_changes_invalidate_the_evidence_snapshot(field, value):
    from application.response_assembly import _response_context
    original = _verified_order_result()
    fact = original.facts[0]
    updated = replace(fact, **{field: fact.observed_at if value == 'observed' else value})
    before = _response_context(_board(original))
    after = _response_context(_board(replace(original, facts=(updated,))))
    assert json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True)


@pytest.mark.parametrize('changes', [
    {'missing_requirement_ids': ('missing',)}, {'conflict_keys': ('conflict',)},
    {'partial_delivery_allowed': True}, {'complete': False},
])
def test_coverage_changes_are_visible_to_both_author_and_verifier(changes):
    from tests.test_knowledge_answer_boundary import Verifier
    board = replace(_board(_verified_order_result()), **changes)
    composer, verifier = _Composer('Result is incomplete.'), Verifier(True)
    response = asyncio.run(ResponseAssembler(composer, knowledge_verifier=verifier).assemble(
        board, current_message='Status?'))
    evidence = json.loads(response.evidence_json)
    assert evidence == composer.calls[0]['evidence'] == json.loads(verifier.calls[0][1]['context'])
    for field, value in changes.items():
        assert evidence['coverage'][field] == (list(value) if isinstance(value, tuple) else value)


def test_runtime_provenance_includes_receipts_not_model_selected_markers():
    from application.agent_result import ReceiptRef
    from tests.test_knowledge_answer_boundary import Verifier
    result = replace(_verified_order_result(), action_receipts=(
        ReceiptRef('receipt:1', 'v1', 'operation:1', 'COMMITTED', 'order.action'),))
    response = asyncio.run(ResponseAssembler(_Composer('Done.'), knowledge_verifier=Verifier(True)).assemble(
        _board(result), current_message='Status?'))
    assert response.evidence_refs == ('read:1', 'receipt:1')


@pytest.mark.parametrize('answer', ['Which item do you mean?', 'The available material does not answer that question.'])
def test_available_knowledge_does_not_force_a_citation_on_nonfactual_interaction(answer):
    from tests.test_knowledge_answer_boundary import board, Verifier
    verifier = Verifier(True)
    response = asyncio.run(ResponseAssembler(_Composer(answer), knowledge_verifier=verifier,
        knowledge_source_validator=lambda packs: True).assemble(board('internal'), current_message='Help?'))
    assert response.verified and response.text == answer
    assert response.evidence_refs == ('read',)
