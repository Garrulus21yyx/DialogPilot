"""Production atomic verification, distinct from frozen legacy baseline tests."""
import asyncio
import itertools
import json
from types import SimpleNamespace
import pytest
from services.answer_verifier import AnswerVerifier, VerificationStatus, VerificationReasonCode
from core.model_policy import ModelProfile, ModelRole
from tests.framework_structured_stub import models
from evaluation.framework_capture import FrameworkCapture


def run(verdict='SUPPORTED', need='ANSWERED', *, mutation=None, stop='tool_use', context=None, answer='当前状态未知。'):
    output={'supported': verdict == 'SUPPORTED', 'answered': need != 'MISSING',
            'approval_terms_complete': False,
            'issues': [] if verdict == 'SUPPORTED' and need != 'MISSING' else ['缺少状态证据或尚未回答问题']}
    if mutation: mutation(output)
    capture = FrameworkCapture(limit=1)
    verifier=AnswerVerifier(models(output, name='submit_claim_checks', stop=stop)[ModelRole.INTENT],
        model_profile=ModelProfile('test'), callbacks=(capture,))
    evidence=context or {'status':'unknown','version':1}
    result=asyncio.run(verifier.verify('查状态',answer,json.dumps(evidence)))
    return result,[c['request'] for c in capture.calls]


@pytest.mark.parametrize('label,need',itertools.product(
    ['SUPPORTED','CONTRADICTED','INSUFFICIENT'],['ANSWERED','LIMITATION','MISSING']))
def test_program_aggregates_every_supported_label_and_need_combination(label,need):
    result,_=run(label,need)
    assert result.publishable==(label=='SUPPORTED' and need!='MISSING')
    assert result.grounded==(label=='SUPPORTED')
    assert result.assessment is not None
    if label!='SUPPORTED':
        assert result.reason_code is VerificationReasonCode.UNGROUNDED
    elif need=='MISSING':
        assert result.reason_code is VerificationReasonCode.INCOMPLETE


@pytest.mark.parametrize('mutate',[
    lambda o:o.update(status='pass'),
    lambda o:o.update(supported='true'),
    lambda o:o.pop('answered'),
    lambda o:o.update(approval_terms_complete=1),
    lambda o:o.update(issues=['']),
    lambda o:o.update(supported=False, issues=[]),
])
def test_invalid_material_is_unknown_never_publishable(mutate):
    result,_=run(mutation=mutate)
    assert result.status is VerificationStatus.UNKNOWN and not result.publishable


def test_truncated_checks_are_unknown():
    result,_=run(stop='max_tokens')
    assert result.status is VerificationStatus.UNKNOWN and not result.publishable


@pytest.mark.parametrize('pending,fields,terms,supported,answered', itertools.product([False, True], repeat=5))
def test_approval_readiness_is_part_of_the_single_verification_verdict(pending, fields, terms, supported, answered):
    result, requests = run(context={'pending_actions': [{'action': 'change'}] if pending else [],
        'requested_inputs': [{'target_work_item_id': 'independent', 'question': 'Which color?'}] if fields else []},
        mutation=lambda o: o.update(supported=supported, answered=answered,
            approval_terms_complete=terms, issues=[] if supported and answered else ['Revise the answer']))
    assert result.publishable is (supported and answered and (not pending or terms))
    # The judge receives the requirement explicitly, not an instruction to
    # rediscover application state from prose or nested business data.
    content = requests[0]['messages'][-1]['content']
    if isinstance(content, list):
        content = ''.join(block['text'] for block in content if block.get('type') == 'text')
    assert json.loads(content)['evidence']['approval_required'] is pending
    system = requests[0]['system']
    assert ('Current turn includes an accepted information request.' in system) is fields
    assert ('Current turn: approve the prepared action.' in system) is pending
    assert 'missing information, not execution approval' not in system
    if pending and not terms and supported and answered:
        assert result.reason_code is VerificationReasonCode.APPROVAL_REQUIRED
        assert result.assessment is not None


def test_retained_approval_is_not_presented_again_when_answering_another_question():
    result, requests = run(context={'pending_actions': [], 'requested_inputs': [],
        'conversation_context': {'retained_approval': {'status': 'AWAITING_DECISION_NOT_EXECUTED'}}})
    assert result.publishable
    content = requests[0]['messages'][-1]['content']
    assert not json.loads(content)['evidence']['approval_required']


@pytest.mark.parametrize('invalid', [object(), float('nan'), {'bad': object()}])
def test_invalid_input_snapshot_is_typed_without_calling_provider(invalid):
    verifier = AnswerVerifier(SimpleNamespace(), model_profile=ModelProfile('test'))
    result = asyncio.run(verifier.verify('查状态', '无法确认。', task_plan=invalid))
    assert result.reason_code is VerificationReasonCode.INVALID_CONTRACT
    assert not result.publishable


def test_assessment_binds_final_answer_and_original_evidence():
    result,calls=run()
    request=json.loads(calls[0]['messages'][0]['content'])
    assert result.assessment.matches('查状态',request['answer'],request['evidence'])
    assert not result.assessment.matches('查状态',request['answer']+'保证退款。',request['evidence'])
    request['evidence']['context']['version']=2
    assert not result.assessment.matches('查状态',request['answer'],request['evidence'])


def test_honest_partial_answer_can_pass_without_completing_business():
    output = {"supported": True, "answered": True, "approval_terms_complete": False, "issues": []}
    verifier = AnswerVerifier(models(output, name="submit_claim_checks")[ModelRole.INTENT],
                              model_profile=ModelProfile("test"))
    result = asyncio.run(verifier.verify("查状态", "暂时无法查询状态。",
        coverage={"complete": False, "unresolved_required_task_ids": ["query"]}))
    assert result.publishable


def test_legacy_preverified_abstention_cannot_authorize_free_text():
    class Messages:
        async def create(self,**request):pytest.fail('unsupported legacy mode must be rejected')
    verifier=AnswerVerifier(SimpleNamespace(),model_profile=ModelProfile('test'))
    result=asyncio.run(verifier.verify('查退款','您仍有权退款',knowledge_evidence={
        'mode':'grounded_final','grounded_answer':'您仍有权退款',
        'abstained':True,'reason':'insufficient_evidence','conflicts':[]}))
    assert not result.publishable
    assert result.reason_code is VerificationReasonCode.INVALID_CONTRACT
