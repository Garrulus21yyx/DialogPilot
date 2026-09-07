import asyncio
import pytest

from application.agent_result import (
    AgentResult,
    AgentResultStatus,
    ReceiptRef,
)
from application.response_assembly import ResponseAssembler, ResponseAssemblyMode
from application.result_board import ResultBoardSnapshot
from tests.test_knowledge_answer_boundary import Verifier


def _result(
    work_item_id, owner, status=AgentResultStatus.SUCCEEDED,
    response=None, reason="DONE", receipts=(),
):
    return AgentResult(
        work_item_id, owner, status, reason, "worker-v1",
        action_receipts=receipts, candidate_response=response,
        retryable=status is AgentResultStatus.RETRYABLE_FAILURE,
    )


def _board(*results, missing=(), conflicts=(), partial=False):
    return ResultBoardSnapshot(
        results, (), (), (), missing, conflicts, True, partial,
    )


def _verified_order_result(work_item_id='o', order_id='DP1234', response=None):
    import json
    from dataclasses import replace
    from datetime import datetime, timezone
    from application.agent_result import FactRecord, FactSourceKind
    fact=FactRecord('order:'+order_id,'order.current_state',
        json.dumps({'order_id':order_id,'status':'shipped'},sort_keys=True,separators=(',',':')),
        FactSourceKind.VERIFIED_STATE,'read:1','order_lookup','order-view-v2',datetime.now(timezone.utc))
    return replace(_result(work_item_id,'order_logistics',response=response),facts=(fact,))


def test_question_prelude_preserves_verified_success_without_publishing_worker_drafts():
    board = _board(_verified_order_result(response="UNVERIFIED promise"),
                   _result("blocked", "product_technical", AgentResultStatus.BLOCKED,
                           response="UNVERIFIED failure explanation"))
    text = ResponseAssembler.interaction_prelude(board)
    assert "DP1234" in text
    assert "UNVERIFIED" not in text
    assert len(board.results) == 2


class _Composer:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def compose(self, payload):
        self.calls.append(payload)
        value = self.response(payload) if callable(self.response) else self.response
        from application.response_assembly import AllowedClaim
        from evaluation.legacy_composition_output import convert_valid_legacy_composition
        claims = [AllowedClaim(c['claim_id'], c['kind'], c['value'], tuple(c['source_refs'])) for c in payload['allowed_claims']]
        return convert_valid_legacy_composition(value, claims)


@pytest.mark.parametrize('repaired', [False, True])
def test_incomplete_approval_uses_one_existing_revision_and_rechecks(repaired):
    from dataclasses import replace
    from langchain_core.messages import AIMessage
    from tests.framework_structured_stub import StructuredStub
    from tests.test_write_workflow import _item
    from services.answer_verifier import AnswerVerifier
    from core.model_policy import ModelProfile
    from application.response_assembly import AssembledResponse
    outputs = [{'supported': True, 'answered': True, 'approval_terms_complete': complete,
                'issues': [] if complete else ['Describe the action and request confirmation']}
               for complete in [False, repaired]]
    model = StructuredStub(responses=[AIMessage(content='', tool_calls=[{
        'name': 'submit_claim_checks', 'id': f'check-{i}', 'args': {'result': value}}])
        for i, value in enumerate(outputs)])
    action = _item()
    board = _board(replace(_result('proposal', action.owner_agent, AgentResultStatus.WAITING_APPROVAL),
                           pending_action=action))
    original = AssembledResponse('Ready.', ResponseAssemblyMode.PASS_THROUGH, (), False, 'PENDING', 'DRAFT')
    feedback = []
    class Assembler(ResponseAssembler):
        async def _assemble_candidate(self, *args, **kwargs):
            feedback.append(kwargs['repair_feedback'])
            return replace(original, text='Please approve the described refund.')
    assembler = Assembler(object(), knowledge_verifier=AnswerVerifier(model, model_profile=ModelProfile('test')))
    candidate, verdict = asyncio.run(assembler._verify_with_revision(board, 'Refund it', original))
    assert len(feedback) == 1
    assert feedback[0]['reason_code'] == 'approval_required'
    assert feedback[0]['assessment']['approval_terms_complete'] is False
    assert candidate.text != original.text
    assert verdict.publishable is repaired
    assert board.results[0].pending_action == action


def test_single_complete_candidate_passes_through_without_composer_call():
    composer = _Composer(AssertionError("composer must not run"))
    assembled = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True)).assemble(
        _board(_result("w1", "order_logistics", response="订单已发货。")),
        current_message="查订单",
    ))

    assert assembled.mode is ResponseAssemblyMode.PASS_THROUGH
    assert assembled.text == "订单已发货。"
    assert composer.calls == []


@pytest.mark.parametrize('repaired', [False, True])
@pytest.mark.parametrize('status', [AgentResultStatus.PARTIAL, AgentResultStatus.RETRYABLE_FAILURE])
def test_unrepresented_outcome_gets_one_revision_without_injecting_server_prose(repaired, status):
    from langchain_core.messages import AIMessage
    from tests.framework_structured_stub import StructuredStub
    from services.answer_verifier import AnswerVerifier
    from core.model_policy import ModelProfile
    from application.response_assembly import AssembledResponse
    from dataclasses import replace
    output = {'supported': True, 'answered': True, 'approval_terms_complete': False, 'issues': []}
    model = StructuredStub(responses=[AIMessage(content='', tool_calls=[{
        'name': 'submit_claim_checks', 'id': f'check-{i}', 'args': {'result': output}}]) for i in range(2)])
    board = _board(_result('done', 'order_logistics'), _result('waiting', 'general', status), partial=True)
    candidate = AssembledResponse('The order lookup finished.', ResponseAssemblyMode.CONVERSATION_COMPOSE,
                                 ('outcome:done',), True, 'PENDING', 'DRAFT')
    feedback = []

    class Assembler(ResponseAssembler):
        async def _assemble_candidate(self, *args, **kwargs):
            feedback.append(kwargs['repair_feedback'])
            return replace(candidate, text='The order lookup finished. The other request is not complete.',
                used_claim_ids=('outcome:done', 'outcome:waiting') if repaired else candidate.used_claim_ids)

    assembler = Assembler(object(), knowledge_verifier=AnswerVerifier(model, model_profile=ModelProfile('test')))
    response, verdict = asyncio.run(assembler._verify_with_revision(board, 'Please handle both requests.', candidate))
    assert len(feedback) == 1
    assert feedback[0]['reason_code'] == 'incomplete'
    assert 'waiting' in feedback[0]['reason']
    assert verdict.publishable is repaired
    assert response.text == 'The order lookup finished. The other request is not complete.'


def test_multi_result_composition_receives_only_claims_and_outcomes():
    composer = _Composer(lambda payload: {
        "segments": [{"text": "订单已发货；商品查询暂时失败。", "claim_ids": [
            item["claim_id"] for item in payload["allowed_claims"]
        ], "evidence_ids": []}],
    })
    board = _board(
        _result("w1", "order_logistics", response="订单已发货。"),
        _result(
            "w2", "product_technical", AgentResultStatus.TERMINAL_FAILURE,
            reason="CATALOG_UNAVAILABLE",
        ),
        partial=True,
    )

    assembled = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True)).assemble(
        board, current_message="查订单和商品",
    ))

    assert assembled.mode is ResponseAssemblyMode.CONVERSATION_COMPOSE
    assert assembled.composer_used is True
    assert set(composer.calls[0]) == {
        "schema_version", "current_message", "conversation_context", "allowed_claims", "support_catalog",
        "work_item_outcomes", "missing_requirement_ids",
        "partial_delivery_allowed", "response_requirements",
    }
    assert any("language they use or request" in requirement
               for requirement in composer.calls[0]["response_requirements"])
    assert any("drafting notes" in requirement
               for requirement in composer.calls[0]["response_requirements"])


def test_unsupported_reference_from_composer_falls_back_without_losing_results():
    composer = _Composer({
        "segments": [{"text": "订单 DP9999 已发货。",
        "claim_ids": ["outcome:w1", "outcome:w2"], "evidence_ids": []}],
    })
    board = _board(
        _verified_order_result('w1'),
        _result("w2", "product_technical", response="商品信息已找到。"),
    )

    assembled = asyncio.run(ResponseAssembler(composer).assemble(
        board, current_message="继续查询",
    ))

    assert assembled.mode is ResponseAssemblyMode.TEMPLATE
    assert assembled.composer_used is False
    assert "DP1234" in assembled.text
    assert "商品信息已找到" not in assembled.text  # Unverified candidate is not a fallback fact.
    assert "DP9999" not in assembled.text
    assert assembled.verification_reason == "COMPOSER_FALLBACK"


def test_committed_receipt_uses_deterministic_template():
    composer = _Composer(AssertionError("composer must not run"))
    receipt = ReceiptRef(
        "receipt-123", "receipt-v1", "operation-123", "COMMITTED",
        "refund.request_action",
    )

    assembled = asyncio.run(ResponseAssembler(composer).assemble(
        _board(_result("w1", "billing_refund", receipts=(receipt,))),
        current_message="申请退款",
    ))

    assert assembled.mode is ResponseAssemblyMode.TEMPLATE
    assert assembled.text == "操作已完成，凭证号：receipt-123。"
    assert composer.calls == []


def test_direct_tool_facts_compose_without_promoting_model_payload_to_reply():
    from dataclasses import replace
    from mcp.tool_manager import ToolResult
    from infrastructure.target_tool_execution import TargetToolExecutor
    from tests.test_target_framework_agent import _context, _item
    item = replace(_item(allowed_tools=('order_lookup',)), requirement_ids=('order.current_state',))
    class Tools:
        async def execute_for_agent(self, *args, **kwargs):
            return ToolResult(True, {'order_id':'DP9301','status':'shipped','internal_debug':'do not display'},
                'order_lookup', authority='order.current_state',call_id='call:1',
                output_for_model='RAW_MODEL_ONLY_JSON')
    result = asyncio.run(TargetToolExecutor(Tools())(_context(item)))
    assert result.candidate_response is None
    composer = _Composer(lambda payload: {'segments':[{'text':'订单 DP9301 已发货。',
        'claim_ids':[c['claim_id'] for c in payload['allowed_claims']], 'evidence_ids':[]}]})
    response = asyncio.run(ResponseAssembler(composer, knowledge_verifier=Verifier(True)).assemble(_board(result),current_message='查订单'))
    assert response.composer_used
    assert 'RAW_MODEL_ONLY_JSON' not in str(composer.calls)
    assert 'internal_debug' in str(composer.calls)  # Original fact is retained, not a lossy projection.
    # Legacy persisted direct results must take the same rendering boundary.
    for direct in (result, replace(result,candidate_response='RAW_MODEL_ONLY_JSON')):
        fallback = asyncio.run(ResponseAssembler().assemble(_board(direct),current_message='查订单'))
        assert fallback.text == '订单 DP9301 当前状态为已发货。'
        assert 'internal_debug' not in fallback.text


def test_fallback_outcome_messages_cover_all_non_success_states_without_internal_codes():
    from application.agent_result import MissingInputSpec, EvidenceRequest
    for status in AgentResultStatus:
        if status in (AgentResultStatus.SUCCEEDED, AgentResultStatus.PARTIAL):
            continue
        result = AgentResult('w1','order_logistics',status,'INTERNAL_TOOL_ERROR','target-tool-executor-v1',
            missing_inputs=(MissingInputSpec('order_id','w1','MISSING','string','订单号？'),)
                if status is AgentResultStatus.NEEDS_USER_INPUT else (),
            requested_evidence=(EvidenceRequest('order.current_state','w1',('orders',)),)
                if status is AgentResultStatus.NEEDS_EVIDENCE else (),
            retryable=status is AgentResultStatus.RETRYABLE_FAILURE)
        response=asyncio.run(ResponseAssembler().assemble(_board(result),current_message='查订单'))
        assert response.text.startswith('订单与物流查询：')
        assert 'INTERNAL_TOOL_ERROR' not in response.text and 'order_logistics' not in response.text
        assert status.value not in response.text


def test_captured_mixed_tools_preserve_business_state_during_knowledge_abstention():
    import gzip
    import json
    from pathlib import Path
    from dataclasses import replace
    from infrastructure.target_agent_result_adapter import restore_framework_artifact
    from infrastructure.target_tool_execution import TargetToolExecutor
    from tests.test_target_framework_agent import _context, _item
    capture=Path(__file__).resolve().parents[1]/'artifacts/eval/rag-mixed-business-2026-09-06-v3/mixed-cases.jsonl.gz'
    rows=[json.loads(line) for line in gzip.decompress(capture.read_bytes()).splitlines()]
    for row in rows:
        results=[]
        for recorded in row['tools']:
            restored=restore_framework_artifact({'schema':'tool-result-v1','result':recorded['result']})
            class Tools:
                async def execute_for_agent(self,*args,**kwargs):
                    return restored
            item=replace(_item(allowed_tools=(recorded['name'],)),
                requirement_ids=(restored.authority,),
                owner_agent='order_logistics' if recorded['name']=='order_lookup' else 'general')
            results.append(asyncio.run(TargetToolExecutor(Tools())(_context(item))))
        response=asyncio.run(ResponseAssembler().assemble(_board(*results),current_message=row['message']))
        assert response.verification_reason=='KNOWLEDGE_SAFE_ABSTENTION'
        assert '"data"' not in response.text and 'output_schema_version' not in response.text
        assert 'order_logistics' not in response.text and 'TOOL_ERROR' not in response.text
        if row['case_id']=='missing':
            assert '本次查询或处理失败' in response.text
        else:
            order=row['tools'][0]['result']['data']
            assert order['order_id'] in response.text
            label={'shipped':'已发货','paid':'已支付','delivered':'已送达'}[order['status']]
            assert label in response.text


def test_later_failure_and_multiple_receipts_do_not_hide_verified_state():
    from dataclasses import replace
    from datetime import datetime, timezone
    import json
    from application.agent_result import FactRecord, FactSourceKind
    fact=FactRecord('order:DP9301','order.current_state',json.dumps({'order_id':'DP9301','status':'shipped'},sort_keys=True,separators=(',',':')),
        FactSourceKind.VERIFIED_STATE,'read:1','order_lookup','order-view-v2',datetime.now(timezone.utc))
    receipts=tuple(ReceiptRef('receipt-'+str(i),'v1','op-'+str(i),'COMMITTED','refund.action') for i in (1,2))
    for status in (AgentResultStatus.SUCCEEDED,AgentResultStatus.PARTIAL,AgentResultStatus.RETRYABLE_FAILURE,AgentResultStatus.RECONCILING):
        result=replace(_result('w','order_logistics',status,receipts=receipts),facts=(fact,))
        response=asyncio.run(ResponseAssembler().assemble(_board(result),current_message='处理请求'))
        assert '已发货' in response.text
        assert all(receipt.receipt_id in response.text for receipt in receipts)
        if status is AgentResultStatus.RETRYABLE_FAILURE:
            assert '失败' in response.text
        if status is AgentResultStatus.RECONCILING:
            assert '正在核实' in response.text


def test_internal_attribution_is_not_a_customer_citation_and_failure_is_always_rendered():
    from application.response_assembly import _allowed_claims
    knowledge=_result('k','general',response='政策证据')
    failed=_result('o','order_logistics',AgentResultStatus.RETRYABLE_FAILURE)
    claims=_allowed_claims(_board(knowledge,failed))
    text,used=ResponseAssembler.prepare_composed_response(
        '政策说明 [outcome:k] [E123abc]',('outcome:k',),claims,'查询订单与政策',
        [{'work_item_id':'o','owner_agent':'order_logistics','status':'RETRYABLE_FAILURE'}])
    assert '[E123abc]' in text and '[outcome:k]' not in text
    assert '本次查询或处理失败' not in text
    assert 'outcome:o' not in used


def test_unknown_internal_attribution_is_rejected_instead_of_hidden():
    import pytest
    from application.response_assembly import _allowed_claims
    claims=_allowed_claims(_board(_result('o','orders',response='查询结果')))
    with pytest.raises(ValueError,match='unknown internal citation'):
        ResponseAssembler.prepare_composed_response('结果 [fact:invented]',('outcome:o',),claims,'查询',[])


def test_outcome_notice_cannot_disagree_with_authoritative_result():
    import pytest
    from application.response_assembly import _allowed_claims
    claims=_allowed_claims(_board(_result('o','orders',AgentResultStatus.TERMINAL_FAILURE)))
    with pytest.raises(ValueError,match='conflicts with its authoritative claim'):
        ResponseAssembler.prepare_composed_response('政策说明',('outcome:o',),claims,'查询',
            [{'work_item_id':'o','owner_agent':'orders','status':'RETRYABLE_FAILURE'}])
