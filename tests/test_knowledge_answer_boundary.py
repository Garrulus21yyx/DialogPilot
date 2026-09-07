import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from application.agent_result import AgentResultStatus
from application.response_assembly import ResponseAssembler
from application.knowledge_tool_contract import evidence_id
from infrastructure.target_agent_result_adapter import fact_from_tool_result
from mcp.tool_manager import MCPToolManager, ToolResult
from tests.test_knowledge_tool_contract import evidence_result
from tests.test_target_framework_agent import _item
from infrastructure.target_framework_agent import _adapt_framework_result
from tests.test_target_framework_agent import _context


def board(answer):
    item=replace(_item(),requirement_ids=('knowledge.active_source',))
    tool=ToolResult(True,evidence_result(),'knowledge_search',authority='knowledge.active_source',call_id='read')
    result=_adapt_framework_result(_context(item),(tool,),'framework-v1',
        allowed_authorities={'knowledge_search': 'knowledge.active_source'},
        candidate_response=answer or "Evidence retrieved.")
    if not answer:
        result = replace(result, candidate_response=None)
    return SimpleNamespace(results=(result,), conflict_keys=(), missing_requirement_ids=(), partial_delivery_allowed=False)


class Verifier:
    def __init__(self, passed): self.passed=passed; self.calls=[]
    async def verify(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        from services.answer_verifier import VerificationResult, VerificationStatus, VerificationReasonCode
        from services.claim_verification import AnswerAssessment
        question, answer = args[:2]
        assessment = AnswerAssessment('fixture', self.passed, True, False,
                                      () if self.passed else ('evidence',))
        return VerificationResult(
            VerificationStatus.PASS if self.passed else VerificationStatus.REJECT,
            self.passed, not self.passed, 'test',
            VerificationReasonCode.PASSED if self.passed else VerificationReasonCode.UNGROUNDED,
            assessment=assessment,
        ).bind_to(*args, **kwargs)


def test_knowledge_model_view_keeps_middle_evidence_without_trace_or_char_truncation():
    text='a'*3500+'TAIL_CONDITION'+'b'*3500
    data=evidence_result(text)
    data['trace']={'debug':'x'*5000}
    manager=MCPToolManager('test-key',model='test-model')
    rendered=manager._render_for_model(ToolResult(True,data,'knowledge_search',authority='knowledge.active_source'))
    assert json.loads(rendered)['evidence'][0]['text']==text
    assert 'debug' not in rendered and 'truncated' not in rendered


def test_citations_and_semantic_support_are_both_required_for_single_result():
    citation='['+evidence_id('child-1')+']'
    class Author:
        def __init__(self, text, invalid=False): self.text, self.invalid = text, invalid
        async def compose(self, payload):
            return {'segments': [{'text': self.text, 'support_ids': ['Sunknown'] if self.invalid else [
                s['support_id'] for s in payload['support_catalog'] if s['evidence_id']]}]}
    for text,passed,invalid in [('所有订单均可退。',False,False),('仅未拆封可退。',True,True)]:
        verifier=Verifier(passed)
        result=asyncio.run(ResponseAssembler(Author(text,invalid), knowledge_verifier=verifier, knowledge_source_validator=lambda packs: True).assemble(board('internal notes'),current_message='能退吗'))
        assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
        assert text not in result.text
    verifier=Verifier(True)
    text='仅未拆封商品可退。 '+citation
    result=asyncio.run(ResponseAssembler(Author('仅未拆封商品可退。'), knowledge_verifier=verifier, knowledge_source_validator=lambda packs: True).assemble(board('internal notes'),current_message='能退吗'))
    assert result.text==text
    assert result.verification_reason=='KNOWLEDGE_SUPPORT_CHECKED'
    assert 'summary' not in verifier.calls[0][1]['context']


def test_verifier_absence_does_not_pass_unverified_policy_answer():
    result=asyncio.run(ResponseAssembler().assemble(board('所有订单均可退。'),current_message='能退吗'))
    assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'


def test_knowledge_failure_preserves_independent_result_and_committed_receipt():
    from tests.test_response_assembly import _result, _board, _verified_order_result
    from application.agent_result import ReceiptRef
    failed = _result('k', 'knowledge', AgentResultStatus.BLOCKED, reason='KNOWLEDGE_NO_EVIDENCE')
    order = _verified_order_result(response='我们会主动跟进。')
    for knowledge in (failed, board('无依据的政策结论').results[0]):
        receipt = ReceiptRef('receipt-123', 'v1', 'op-123', 'COMMITTED', 'refund.action')
        knowledge = replace(knowledge, action_receipts=(receipt,))
        result = asyncio.run(ResponseAssembler().assemble(_board(order, knowledge), current_message='订单和退款'))
        assert '已发货' in result.text and '请求已提交' in result.text
        assert 'receipt-123' not in result.text
        assert knowledge.action_receipts == (receipt,)
        assert '主动跟进' not in result.text
        assert '无依据的政策结论' not in result.text


def test_mixed_composer_receives_same_evidence_ids_as_publication_gate():
    from tests.test_response_assembly import _result, _board, _Composer
    citation = '[' + evidence_id('child-1') + ']'
    composer = _Composer(lambda payload: {
        'segments': [{'text': '仅未拆封商品可退。',
        'claim_ids': [c['claim_id'] for c in payload['allowed_claims']], 'evidence_ids':[evidence_id('child-1')]}],
    })
    mixed = _board(board('draft').results[0], _result('o','orders',response='订单已发货。'))
    result = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True), knowledge_source_validator=lambda packs: True).assemble(mixed,current_message='能退吗'))
    assert result.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'
    assert evidence_id('child-1') in json.dumps(composer.calls[0])


def test_withdrawal_during_semantic_verification_prevents_publication():
    from tests.test_response_assembly import _Composer
    live = [True]
    class WithdrawDuringVerify(Verifier):
        async def verify(self,*args,**kwargs):
            live[0] = False
            return await super().verify(*args,**kwargs)
    citation='['+evidence_id('child-1')+']'
    text='仅未拆封商品可退。 '+citation
    composer = _Composer(lambda p: {'segments': [{
        'text': '仅未拆封商品可退。',
        'claim_ids': [c['claim_id'] for c in p['allowed_claims']],
        'evidence_ids': [evidence_id('child-1')]}]})
    result=asyncio.run(ResponseAssembler(composer, knowledge_verifier=WithdrawDuringVerify(True),
        knowledge_source_validator=lambda packs: live[0]).assemble(board(text),current_message='能退吗'))
    assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
    assert text not in result.text
    assert len(composer.calls) == 1 and not live[0]


def test_factless_business_outcomes_never_disappear_into_pure_knowledge_generation():
    from tests.test_response_assembly import _result, _board, _Composer
    citation = '[' + evidence_id('child-1') + ']'
    knowledge = replace(board('draft').results[0], producer_version='target-tool-executor-v1')
    for status in AgentResultStatus:
        from application.agent_result import AgentResult, MissingInputSpec, EvidenceRequest
        missing = (MissingInputSpec('order_id', 'business', 'MISSING', 'string', '订单号？'),) if status is AgentResultStatus.NEEDS_USER_INPUT else ()
        requested = (EvidenceRequest('order.current_state', 'business', ('orders',)),) if status is AgentResultStatus.NEEDS_EVIDENCE else ()
        business = AgentResult('business', 'orders', status, 'TEST_OUTCOME', 'target-tool-executor-v1',
            missing_inputs=missing, requested_evidence=requested,
            retryable=status is AgentResultStatus.RETRYABLE_FAILURE)
        composer = _Composer(lambda payload: {
            'segments':[{'text': '订单查询结果需单独处理。仅未拆封商品可退。',
            'claim_ids': [c['claim_id'] for c in payload['allowed_claims']], 'evidence_ids':[evidence_id('child-1')]}],
        })
        verifier = Verifier(True)
        result = asyncio.run(ResponseAssembler(composer,
            knowledge_verifier=verifier, knowledge_source_validator=lambda packs: True).assemble(
                _board(knowledge, business), current_message='查订单并说明退货政策'))
        assert result.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'
        assert len(composer.calls) == 1
        assert composer.calls[0]['work_item_outcomes'][1]['status'] == status.value
        assert verifier.calls[0][1]['agent_outcomes'][1]['status'] == status.value


def test_successful_direct_knowledge_uses_the_same_conversation_author():
    from tests.test_response_assembly import _board, _Composer
    composer = _Composer(lambda payload: {'segments': [{
        'text': '仅未拆封商品可退。',
        'claim_ids': [c['claim_id'] for c in payload['allowed_claims']],
        'evidence_ids': [evidence_id('child-1')]}]})
    knowledge = replace(board('draft').results[0], producer_version='target-tool-executor-v1')
    result = asyncio.run(ResponseAssembler(composer,
        knowledge_verifier=Verifier(True), knowledge_source_validator=lambda packs: True).assemble(
            _board(knowledge), current_message='退货政策是什么'))
    assert len(composer.calls) == 1
    assert result.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'


def test_support_verifier_sees_final_rendered_text_including_failed_business_outcome():
    from tests.test_response_assembly import _result, _board, _Composer
    citation='['+evidence_id('child-1')+']'
    failed=_result('missing-order','order_logistics',AgentResultStatus.RETRYABLE_FAILURE,reason='TOOL_ERROR')
    knowledge=board('draft').results[0]
    mixed=_board(knowledge,failed)
    def answer(payload):
        claim=next(c['claim_id'] for c in payload['allowed_claims'] if c['kind']=='KNOWLEDGE_FACT')
        return {'segments':[{'text':'仅未拆封商品可退。','claim_ids':[claim],'evidence_ids':[evidence_id('child-1')]},
            {'text':'本次查询或处理失败，请稍后再试。','claim_ids':['outcome:missing-order'],'evidence_ids':[]}]}
    verifier=Verifier(True)
    response=asyncio.run(ResponseAssembler(_Composer(answer),knowledge_verifier=verifier,
        knowledge_source_validator=lambda packs:True).assemble(mixed,current_message='查询订单并说明退货政策'))
    checked=verifier.calls[0][0][1]
    assert checked==response.text
    assert '本次查询或处理失败' in checked
    assert citation in checked and '[fact:' not in checked


def test_context_reaches_mixed_composition_and_verifier_separate_from_facts():
    from tests.test_response_assembly import _board, _verified_order_result, _Composer
    context = {'recent_messages': [{'role': 'assistant', 'content': '是质量问题吗？', 'source_ref': 'turn:2'}]}
    composer = _Composer(lambda payload: {'segments': [{'text': '订单已发货。',
        'claim_ids': [next(c['claim_id'] for c in payload['allowed_claims'] if c['kind'] == 'FACT')], 'evidence_ids': []}]})
    verifier = Verifier(True)
    asyncio.run(ResponseAssembler(composer, knowledge_verifier=verifier).assemble(
        _board(_verified_order_result()), current_message='不是。查订单', conversation_context=context))
    assert composer.calls[0]['conversation_context'] == context
    assert all('是质量问题吗' not in str(c) for c in composer.calls[0]['allowed_claims'])
    support = json.loads(verifier.calls[0][1]['context'])
    assert support['user_context'] == context
    assert all('是质量问题吗' not in str(c) for c in support['facts'])


def test_direct_knowledge_composition_and_support_receive_same_context():
    from tests.test_response_assembly import _board, _Composer
    context = {'recent_messages': [{'role': 'assistant', 'content': '是质量问题吗？', 'source_ref': 'turn:2'}]}
    composer = _Composer(lambda payload: {'segments': [{
        'text': '仅未拆封可退。',
        'claim_ids': [c['claim_id'] for c in payload['allowed_claims']],
        'evidence_ids': [evidence_id('child-1')]}]})
    knowledge = replace(board('draft').results[0], producer_version='target-tool-executor-v1')
    verifier = Verifier(True)
    answer = asyncio.run(ResponseAssembler(composer, knowledge_verifier=verifier,
        knowledge_source_validator=lambda packs: True).assemble(_board(knowledge),
        current_message='不是。', conversation_context=context))
    assert answer.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'
    assert composer.calls[0]['current_message'] == '不是。'
    assert composer.calls[0]['conversation_context'] == context
    assert evidence_id('child-1') in json.dumps(composer.calls[0]['support_catalog'])
    assert json.loads(verifier.calls[0][1]['context'])['user_context'] == context
