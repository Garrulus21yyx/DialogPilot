"""Question semantics and evidence attribution are independent contracts."""
import asyncio
from itertools import product

import pytest
from jsonschema import Draft202012Validator
from langchain_core.messages import AIMessage

from application.agent_result import AgentResult, AgentResultStatus, MissingInputSpec
from application.composition_output import composition_schema, render_composition
from application.response_assembly import ResponseAssembler, _allowed_claims
from core.model_policy import ModelProfile
from services.answer_verifier import AnswerVerifier
from tests.framework_structured_stub import StructuredStub
from tests.test_response_assembly import _board


def fixture(count=1, status=AgentResultStatus.NEEDS_USER_INPUT, independent=False):
    specs = tuple(MissingInputSpec(f'field{i}', 'w', 'MISSING', 'string', f'Please provide field {i}.')
                  for i in range(count))
    waiting = AgentResult('w', 'retail', status, 'MISSING', 'test', missing_inputs=specs)
    results = [waiting]
    if independent:
        results.append(AgentResult('other', 'retail', AgentResultStatus.TERMINAL_FAILURE, 'TOOL_ERROR', 'test'))
    return _board(*results), specs


@pytest.mark.parametrize('count,answered,supported,independent', product((1, 2, 3), (False, True), (False, True), (False, True)))
def test_bound_question_semantics_not_duplicate_attribution(count, answered, supported, independent):
    board, specs = fixture(count, independent=independent)
    output = {'supported': supported, 'answered': answered, 'approval_terms_complete': False,
              'issues': [] if supported and answered else ['Missing input or unsupported premise.']}
    messages = [AIMessage(content='', tool_calls=[{'name': 'submit_claim_checks',
        'args': {'result': output}, 'id': f'check-{i}', 'type': 'tool_call'}]) for i in range(2)]
    model = StructuredStub(responses=messages)
    verifier = AnswerVerifier(model, model_profile=ModelProfile('test'))
    class Composer:
        async def compose(self, payload):
            assert payload['requested_inputs']
            assert all(c['kind'] != 'INPUT_REQUEST' for c in payload['allowed_claims'])
            return {'segments': [{'text': 'Please provide the requested information.'}]}
    result = asyncio.run(ResponseAssembler(Composer(), knowledge_verifier=verifier).assemble(
        board, current_message='Help me continue.', requested_inputs=specs))
    assert result.verified == (answered and supported and not independent)
    assert not result.retryable
    if result.verified:
        assert result.used_claim_ids == ()


@pytest.mark.parametrize('bound', (False, True))
def test_plain_question_schema_and_renderer_agree(bound):
    board, specs = fixture()
    specs = specs if bound else ()
    claims = _allowed_claims(board, requested_inputs=specs)
    value = {'segments': [{'text': 'Which option do you prefer?'}]}
    errors = list(Draft202012Validator(composition_schema(claims, requested_inputs=specs)).iter_errors(value))
    assert bool(errors) != bound
    if bound:
        assert render_composition(value, claims, requested_inputs=specs) == ('Which option do you prefer?', ())
    else:
        with pytest.raises(ValueError):
            render_composition(value, claims)


@pytest.mark.parametrize('paragraphs', (1, 2, 4, 20))
@pytest.mark.parametrize('supported', (False, True))
def test_interaction_paragraphs_do_not_replace_semantic_validation(paragraphs, supported):
    from tests.test_knowledge_answer_boundary import Verifier
    board, specs = fixture(2)
    value = {'segments': [{'text': 'Please provide the requested information.'} for _ in range(paragraphs)]}
    claims = _allowed_claims(board, requested_inputs=specs)
    Draft202012Validator(composition_schema(claims, requested_inputs=specs)).validate(value)
    assert render_composition(value, claims, requested_inputs=specs)[0].count('Please') == paragraphs
    class Composer:
        async def compose(self, payload):
            return value
    verifier = Verifier(supported)
    result = asyncio.run(ResponseAssembler(Composer(), knowledge_verifier=verifier).assemble(
        board, current_message='Continue', requested_inputs=specs))
    assert result.verified is supported
    assert verifier.calls
    with pytest.raises(ValueError):
        render_composition(value, claims)  # no bound input: ordinary factual contract still applies


@pytest.mark.parametrize('text', ('Question [E123]', 'Question outcome:w', 'Question [Sabc]'))
def test_question_does_not_bypass_citation_checks(text):
    board, specs = fixture()
    with pytest.raises(ValueError):
        render_composition({'segments': [{'text': text}]}, _allowed_claims(board), requested_inputs=specs)


@pytest.mark.parametrize('status', (AgentResultStatus.PARTIAL, AgentResultStatus.BLOCKED,
                                   AgentResultStatus.TERMINAL_FAILURE, AgentResultStatus.NEEDS_USER_INPUT))
def test_only_waiting_state_is_represented_by_its_bound_question(status):
    board, specs = fixture(status=status)
    from tests.test_knowledge_answer_boundary import Verifier
    verifier = Verifier(True)
    asyncio.run(ResponseAssembler(knowledge_verifier=verifier)._verify_support(
        board, 'Continue', 'Please provide the information.', used_claim_ids=(), requested_inputs=specs))
    import json
    context = json.loads(verifier.calls[0][1]['context'])
    assert context['unrepresented_outcomes'] == ([] if status is AgentResultStatus.NEEDS_USER_INPUT else ['w'])


@pytest.mark.parametrize('transient', (False, True))
@pytest.mark.parametrize('stage', ('compose', 'verify'))
def test_error_classification_survives_question_assembly(stage, transient):
    from core.framework_models import ModelInvocationError
    from tests.test_knowledge_answer_boundary import Verifier
    board, specs = fixture(independent=True)
    cause = TimeoutError() if transient else ValueError('invalid provider output')
    class Composer:
        async def compose(self, payload):
            if stage == 'compose':
                raise ModelInvocationError('compose', cause)
            return {'segments': [{'text': 'Which option?'}]}
    class BrokenVerifier(Verifier):
        async def verify(self, *args, **kwargs):
            raise ModelInvocationError('verify', cause)
    result = asyncio.run(ResponseAssembler(Composer(), knowledge_verifier=BrokenVerifier(True)).assemble(
        board, current_message='Continue', requested_inputs=specs))
    assert not result.verified
    assert result.retryable is transient
    expected_stage = 'composition_model' if stage == 'compose' else 'answer_verification'
    assert result.verification_reason == expected_stage + ':ModelInvocationError'
    assert result.diagnostics[0].stage == expected_stage
    assert result.diagnostics[0].detail['retryable'] is transient


@pytest.mark.parametrize('transient', (False, True))
def test_failed_revision_is_not_reverified_or_reclassified(transient):
    from core.framework_models import ModelInvocationError
    from tests.test_knowledge_answer_boundary import Verifier
    board, specs = fixture()
    class Composer:
        calls = 0
        async def compose(self, payload):
            self.calls += 1
            if self.calls == 2:
                raise ModelInvocationError('compose', TimeoutError() if transient else ValueError('invalid'))
            return {'segments': [{'text': 'Unsupported premise.'}]}
    class RejectThenUnavailable(Verifier):
        async def verify(self, *args, **kwargs):
            if self.calls:
                raise ModelInvocationError('verify', TimeoutError())
            return await super().verify(*args, **kwargs)
    verifier = RejectThenUnavailable(False)
    result = asyncio.run(ResponseAssembler(Composer(), knowledge_verifier=verifier).assemble(
        board, current_message='Continue', requested_inputs=specs))
    assert len(verifier.calls) == 1
    assert result.retryable is transient
    assert result.verification_reason == 'composition_model:ModelInvocationError'
    assert result.diagnostics[0].detail['retryable'] is transient


def test_bound_question_passes_actual_provider_sdk_output_contract():
    from dataclasses import asdict
    from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
    from tests.framework_structured_stub import models
    board, specs = fixture(2)
    value = {'segments': [{'text': 'Which size and color would you prefer?'}]}
    provider = AnthropicConversationPlanningProvider(models(value, name='submit_composed_response'),
        model_profile=ModelProfile('test'), synthesis_profile=ModelProfile('test'))
    payload = {'allowed_claims': [asdict(c) for c in _allowed_claims(board)],
               'requested_inputs': [asdict(s) for s in specs]}
    assert asyncio.run(provider.compose(payload)) == value
