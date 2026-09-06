"""Production atomic verification, distinct from frozen legacy baseline tests."""
import asyncio
import itertools
import json
from types import SimpleNamespace
import pytest
from services.answer_verifier import AnswerVerifier, VerificationStatus, VerificationReasonCode


def run(verdict='SUPPORTED', need='ANSWERED', *, mutation=None, stop='tool_use', context=None, answer='当前状态未知。'):
    calls=[]
    class Messages:
        async def create(self, **request):
            calls.append(request)
            payload=json.loads(request['messages'][0]['content'])
            assert payload['answer']==answer
            assert request['max_tokens']==4096
            output={'claim_checks':[{'segment_id':'s1','answer_quote':answer,
                'verdict':verdict,'evidence_paths':['/context/status'],
                'reason':'test','missing_evidence':['状态证据'] if verdict=='INSUFFICIENT' else []}],
                'question_checks':[{'question_quote':'查状态','status':need,
                    'answer_quotes':[answer] if need!='MISSING' else [],'reason':'test'}]}
            if mutation: mutation(output)
            return SimpleNamespace(stop_reason=stop,content=[SimpleNamespace(
                type='tool_use',name='submit_claim_checks',input=output)])
    verifier=AnswerVerifier(client=SimpleNamespace(messages=Messages()),model='test')
    evidence=context or {'status':'unknown','version':1}
    result=asyncio.run(verifier.verify('查状态',answer,json.dumps(evidence)))
    return result,calls


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
    lambda o:o.update(claim_checks=[]),
    lambda o:o.update(question_checks=[]),
    lambda o:o['question_checks'][0].update(answer_quotes=[]),
    lambda o:o['question_checks'][0].update(answer_quotes=['不存在的文本']),
    lambda o:o['claim_checks'][0].update(evidence_paths=['/context/nonexistent']),
])
def test_invalid_material_is_unknown_never_publishable(mutate):
    result,_=run(mutation=mutate)
    assert result.status is VerificationStatus.UNKNOWN and not result.publishable


def test_truncated_checks_are_unknown():
    result,_=run(stop='max_tokens')
    assert result.status is VerificationStatus.UNKNOWN and not result.publishable


@pytest.mark.parametrize('invalid', [object(), float('nan'), {'bad': object()}])
def test_invalid_input_snapshot_is_typed_without_calling_provider(invalid):
    verifier = AnswerVerifier(client=SimpleNamespace(), model='test')
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


def test_known_incomplete_task_never_calls_model():
    class Messages:
        async def create(self,**request):pytest.fail('known incomplete task must not call model')
    verifier=AnswerVerifier(client=SimpleNamespace(messages=Messages()),model='test')
    result=asyncio.run(verifier.verify('查状态','未知',coverage={'complete':False}))
    assert result.reason_code is VerificationReasonCode.INCOMPLETE and not result.publishable


def test_legacy_preverified_abstention_cannot_authorize_free_text():
    class Messages:
        async def create(self,**request):pytest.fail('unsupported legacy mode must be rejected')
    verifier=AnswerVerifier(client=SimpleNamespace(messages=Messages()),model='test')
    result=asyncio.run(verifier.verify('查退款','您仍有权退款',knowledge_evidence={
        'mode':'grounded_final','grounded_answer':'您仍有权退款',
        'abstained':True,'reason':'insufficient_evidence','conflicts':[]}))
    assert not result.publishable
    assert result.reason_code is VerificationReasonCode.INVALID_CONTRACT
