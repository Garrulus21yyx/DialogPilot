import asyncio
import hashlib
from dataclasses import replace
import pytest
from application.response_assembly import ResponseAssembler, AssembledResponse, ResponseAssemblyMode
from services.answer_verifier import VerificationResult, VerificationStatus, VerificationReasonCode
from services.claim_verification import AnswerAssessment
from tests.test_response_assembly import _verified_order_result, _board


class Composer:
    def __init__(self):
        self.calls=[]
    async def compose(self,payload):
        self.calls.append(payload)
        text='订单 DP1234 当前状态为已发货。'
        if 'repair_feedback' not in payload:text+='保证退款。'
        return "\n".join([(text)])


class Verifier:
    def __init__(self, always_reject=False, wrong_binding=False):
        self.calls=[];self.always_reject=always_reject;self.wrong_binding=wrong_binding
    async def verify(self,question,answer,**kwargs):
        self.calls.append(answer)
        passed=not self.always_reject and '保证退款' not in answer
        assessment=AnswerAssessment('fixture', passed, True, () if passed else ('退款依据',))
        result=VerificationResult(VerificationStatus.PASS if passed else VerificationStatus.REJECT,
            passed,not passed,'fixture',VerificationReasonCode.PASSED if passed else VerificationReasonCode.UNGROUNDED,
            assessment=assessment)
        return result.bind_to(question,answer+'tampered' if self.wrong_binding else answer,**kwargs)


def test_one_revision_uses_original_facts_and_rechecks_exact_final_text():
    composer=Composer();verifier=Verifier()
    board=_board(_verified_order_result())
    result=asyncio.run(ResponseAssembler(composer,knowledge_verifier=verifier).assemble(
        board,current_message='查订单状态',system_notice='查询结果：'))
    assert len(composer.calls)==2 and len(verifier.calls)==2
    assert composer.calls[0]['evidence']==composer.calls[1]['evidence']
    assert 'repair_feedback' in composer.calls[1]
    assert '保证退款' not in result.text and result.text==verifier.calls[-1]
    assert result.verified_text_sha256==hashlib.sha256(result.text.encode()).hexdigest()
    with pytest.raises(ValueError):replace(result,text=result.text+'保证退款。')


def test_second_rejection_falls_back_without_third_revision():
    composer=Composer();verifier=Verifier(always_reject=True)
    result=asyncio.run(ResponseAssembler(composer,knowledge_verifier=verifier).assemble(
        _board(_verified_order_result()),current_message='查订单状态'))
    assert len(composer.calls)==2 and len(verifier.calls)==2
    assert result.verification_reason=='ungrounded'
    # This read fixture has no freshness lease. The immutable fallback reports
    # its observed state, not a claim that the order is still in that state now.
    assert '的查询记录中，订单 DP1234 状态为已发货。' in result.text
    assert '当前状态' not in result.text


def test_mismatched_verification_cannot_authorize_candidate_or_revision():
    composer=Composer();verifier=Verifier(wrong_binding=True)
    result=asyncio.run(ResponseAssembler(composer,knowledge_verifier=verifier).assemble(
        _board(_verified_order_result()),current_message='查订单状态'))
    assert len(composer.calls)==1 and len(verifier.calls)==1
    assert result.verification_reason=='answer_verification:ValueError'
    assert '保证退款' not in result.text


def test_worker_free_text_without_verifier_uses_only_fact_fallback():
    from tests.test_response_assembly import _result
    result=asyncio.run(ResponseAssembler().assemble(
        _board(_result('w','billing_refund',response='保证退款。')),current_message='查退款'))
    assert '保证退款' not in result.text
    assert result.verification_reason=='DETERMINISTIC_ASSEMBLY'
    assert not result.composer_used and not result.verified
