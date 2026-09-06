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
from langchain_core.messages import AIMessage


def board(answer):
    item=replace(_item(),requirement_ids=('knowledge.active_source',))
    tool=ToolResult(True,evidence_result(),'knowledge_search',authority='knowledge.active_source',call_id='read')
    result=_adapt_framework_result(_context(item),(tool,),(AIMessage(content=answer),),'framework-v1')
    return SimpleNamespace(results=(result,), conflict_keys=(), missing_requirement_ids=(), partial_delivery_allowed=False)


class Verifier:
    def __init__(self, passed): self.passed=passed; self.calls=[]
    async def verify(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        return SimpleNamespace(publishable=self.passed, grounded=self.passed)


def test_knowledge_model_view_keeps_middle_evidence_without_trace_or_char_truncation():
    text='a'*3500+'TAIL_CONDITION'+'b'*3500
    data=evidence_result(text)
    data['trace']={'debug':'x'*5000}
    manager=MCPToolManager('test-key',model='test-model',max_output_chars=256)
    rendered=manager._render_for_model(ToolResult(True,data,'knowledge_search',authority='knowledge.active_source'))
    assert json.loads(rendered)['evidence'][0]['text']==text
    assert 'debug' not in rendered and 'truncated' not in rendered


def test_citations_and_semantic_support_are_both_required_for_single_result():
    citation='['+evidence_id('child-1')+']'
    for text,passed in [('所有订单均可退。 '+citation,False),('仅未拆封可退。 [Eunknown]',True)]:
        verifier=Verifier(passed)
        result=asyncio.run(ResponseAssembler(knowledge_verifier=verifier, knowledge_source_validator=lambda packs: True).assemble(board(text),current_message='能退吗'))
        assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
        assert text not in result.text
    verifier=Verifier(True)
    text='仅未拆封商品可退。 '+citation
    result=asyncio.run(ResponseAssembler(knowledge_verifier=verifier, knowledge_source_validator=lambda packs: True).assemble(board(text),current_message='能退吗'))
    assert result.text==text
    assert result.verification_reason=='KNOWLEDGE_SUPPORT_CHECKED'
    assert 'summary' not in verifier.calls[0][1]['context']


def test_verifier_absence_does_not_pass_unverified_policy_answer():
    result=asyncio.run(ResponseAssembler().assemble(board('所有订单均可退。'),current_message='能退吗'))
    assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'


def test_knowledge_failure_preserves_independent_result_and_committed_receipt():
    from tests.test_response_assembly import _result, _board
    from application.agent_result import ReceiptRef
    failed = _result('k', 'knowledge', AgentResultStatus.BLOCKED, reason='KNOWLEDGE_NO_EVIDENCE')
    order = _result('o', 'orders', response='订单已发货。')
    for knowledge in (failed, board('无依据的政策结论').results[0]):
        receipt = ReceiptRef('receipt-123', 'v1', 'op-123', 'COMMITTED', 'refund.action')
        knowledge = replace(knowledge, action_receipts=(receipt,))
        result = asyncio.run(ResponseAssembler().assemble(_board(order, knowledge), current_message='订单和退款'))
        assert '订单已发货' in result.text and 'receipt-123' in result.text
        assert '无依据的政策结论' not in result.text


def test_mixed_composer_receives_same_evidence_ids_as_publication_gate():
    from tests.test_response_assembly import _result, _board, _Composer
    citation = '[' + evidence_id('child-1') + ']'
    composer = _Composer(lambda payload: {
        'response': '仅未拆封商品可退。 ' + citation,
        'used_claim_ids': [c['claim_id'] for c in payload['allowed_claims']],
    })
    mixed = _board(board('draft').results[0], _result('o','orders',response='订单已发货。'))
    result = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True), knowledge_source_validator=lambda packs: True).assemble(mixed,current_message='能退吗'))
    assert result.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'
    assert evidence_id('child-1') in json.dumps(composer.calls[0])


def test_withdrawal_during_semantic_verification_prevents_publication():
    live = [True]
    class WithdrawDuringVerify(Verifier):
        async def verify(self,*args,**kwargs):
            live[0] = False
            return await super().verify(*args,**kwargs)
    citation='['+evidence_id('child-1')+']'
    text='仅未拆封商品可退。 '+citation
    result=asyncio.run(ResponseAssembler(knowledge_verifier=WithdrawDuringVerify(True),
        knowledge_source_validator=lambda packs: live[0]).assemble(board(text),current_message='能退吗'))
    assert result.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
    assert text not in result.text


def test_factless_business_outcomes_never_disappear_into_pure_knowledge_generation():
    from tests.test_response_assembly import _result, _board, _Composer
    citation = '[' + evidence_id('child-1') + ']'
    class Generator:
        async def generate(self, *args):
            raise AssertionError('mixed outcomes require composition')
    knowledge = replace(board('draft').results[0], producer_version='target-tool-executor-v1')
    for status in AgentResultStatus:
        from application.agent_result import AgentResult, MissingInputSpec, EvidenceRequest
        missing = (MissingInputSpec('order_id', 'business', 'MISSING', 'string', '订单号？'),) if status is AgentResultStatus.NEEDS_USER_INPUT else ()
        requested = (EvidenceRequest('order.current_state', 'business', ('orders',)),) if status is AgentResultStatus.NEEDS_EVIDENCE else ()
        business = AgentResult('business', 'orders', status, 'TEST_OUTCOME', 'target-tool-executor-v1',
            missing_inputs=missing, requested_evidence=requested,
            retryable=status is AgentResultStatus.RETRYABLE_FAILURE)
        composer = _Composer(lambda payload: {
            'response': '订单查询结果需单独处理。仅未拆封商品可退。 ' + citation,
            'used_claim_ids': [c['claim_id'] for c in payload['allowed_claims']],
        })
        verifier = Verifier(True)
        result = asyncio.run(ResponseAssembler(composer, knowledge_generator=Generator(),
            knowledge_verifier=verifier, knowledge_source_validator=lambda packs: True).assemble(
                _board(knowledge, business), current_message='查订单并说明退货政策'))
        assert result.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'
        assert len(composer.calls) == 1
        assert composer.calls[0]['work_item_outcomes'][1]['status'] == status.value
        assert verifier.calls[0][1]['agent_outcomes'][1]['status'] == status.value


def test_successful_direct_knowledge_retains_grounded_generation_path():
    from tests.test_response_assembly import _board, _Composer
    calls = []
    class Generator:
        async def generate(self, query, contexts):
            calls.append((query, contexts))
            return SimpleNamespace(abstained=False, claims=(
                SimpleNamespace(text='仅未拆封商品可退。', citations=('child-1',)),))
    composer = _Composer(AssertionError('pure knowledge should use grounded generator'))
    knowledge = replace(board('draft').results[0], producer_version='target-tool-executor-v1')
    result = asyncio.run(ResponseAssembler(composer, knowledge_generator=Generator(),
        knowledge_verifier=Verifier(True), knowledge_source_validator=lambda packs: True).assemble(
            _board(knowledge), current_message='退货政策是什么'))
    assert len(calls) == 1 and not composer.calls
    assert result.verification_reason == 'KNOWLEDGE_SUPPORT_CHECKED'
