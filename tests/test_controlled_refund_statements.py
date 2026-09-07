import copy
from dataclasses import asdict, replace
import itertools
import json
import pytest
from jsonschema import Draft202012Validator
from services.customer_operation_views import refund_lookup_statements
from services.customer_operations import RefundStatus
from application.composition_output import (
    composition_schema, statement_catalog, support_catalog, prepare_composition_payload,
    render_composition,
)
from application.response_assembly import AllowedClaim, _allowed_claims, _render_board
from tests.test_response_assembly import _verified_order_result, _board


def claims():
    return (AllowedClaim('refund', 'CONTROLLED_REFUND_FACT',
        {'lookup_status':'NO_APPLICATION','order_id':'DP9302','order_version':1}, ('refund_status',)),)


def test_all_fact_ref_choices_render_only_owner_text():
    source=claims();catalog=statement_catalog(source)
    schema=Draft202012Validator(composition_schema(source))
    for count in range(1,len(catalog)+1):
        for selected in itertools.permutations(catalog,count):
            value={'segments':[{'type':'fact_ref','statement_id':s['statement_id']} for s in selected]}
            assert schema.is_valid(value)
            text,used=render_composition(value,source)
            assert text=='\n'.join(s['text'] for s in selected)
            assert used==('refund',)
    assert not schema.is_valid({'segments':[{'text':'您仍有权退款。',
        'support_ids':[support_catalog(source)[0]['support_id']]}]})


def test_refund_source_cannot_be_reworded_or_reused_after_version_change():
    source=claims();sid=statement_catalog(source)[0]['statement_id']
    with pytest.raises(ValueError):
        render_composition({'segments':[{'text':'仍有权退款','support_ids':[support_catalog(source)[0]['support_id']]}]},source)
    with pytest.raises(ValueError):
        render_composition({'segments':[{'type':'fact_ref','statement_id':sid,'text':'仍有权退款'}]},source)
    changed=(replace(source[0],value={**source[0].value,'order_version':2}),)
    with pytest.raises(ValueError):
        render_composition({'segments':[{'type':'fact_ref','statement_id':sid}]},changed)
    payload=prepare_composition_payload({'allowed_claims':[asdict(c) for c in source]})
    payload['statement_catalog'][0]['text']='仍有权退款'
    with pytest.raises(ValueError):prepare_composition_payload(payload)


@pytest.mark.parametrize('status',list(RefundStatus))
def test_all_found_states_have_explicit_record_wording(status):
    result=refund_lookup_statements({'lookup_status':'FOUND','order_id':'DP9302',
                                     'refund_id':'RF1','status':status.value})
    assert len(result)==1
    assert '在本系统的记录状态' in result[0][1]
    assert '到账' not in result[0][1]


@pytest.mark.parametrize('data',[
    {'lookup_status':'UNAVAILABLE','order_id':'DP1'},
    {'lookup_status':'NO_APPLICATION','order_id':'DP1','order_version':True},
    {'lookup_status':'NO_APPLICATION','order_id':'DP1','order_version':1,'status':'refunded'},
    {'lookup_status':'FOUND','order_id':'DP1','refund_id':'RF1','status':'invented'},
])
def test_unknown_and_inconsistent_observations_do_not_render(data):
    with pytest.raises(ValueError):refund_lookup_statements(data)


def test_authoritative_requirement_selects_controlled_path_and_safe_fallback():
    original=_verified_order_result(response='您仍有权退款。')
    fact=replace(original.facts[0],requirement_id='refund.current_state',
                 value_json=json.dumps(claims()[0].value,sort_keys=True,separators=(',',':')))
    board=_board(replace(original,facts=(fact,)))
    from application.response_assembly import ResponseAssembler, ResponseAssemblyMode
    assert ResponseAssembler._select_mode(board) is ResponseAssemblyMode.CONVERSATION_COMPOSE
    allowed=_allowed_claims(board)
    assert [c.kind for c in allowed]==['CONTROLLED_REFUND_FACT']
    assert '仍有权' not in _render_board(board)
    assert '未记录' in _render_board(board)
    assert '无法从当前系统记录确认' in _render_board(board)
    # A lookalike payload under another requirement is not promoted.
    other=_board(replace(original,facts=(replace(fact,requirement_id='order.current_state'),)))
    assert 'CONTROLLED_REFUND_FACT' not in {c.kind for c in _allowed_claims(other)}


def test_partial_refund_outcome_is_expressible_without_promoting_candidate_facts():
    from application.agent_result import AgentResultStatus
    result=_verified_order_result(response='您仍有权退款。')
    fact=replace(result.facts[0],requirement_id='refund.current_state',
        value_json=json.dumps(claims()[0].value,sort_keys=True,separators=(',',':')))
    board=_board(replace(result,status=AgentResultStatus.PARTIAL,facts=(fact,)))
    allowed=_allowed_claims(board)
    outcome=next(c for c in allowed if c.kind=='WORK_ITEM_OUTCOME')
    assert outcome.value['summary'] is None
    assert 'render_mode' not in outcome.value
    assert outcome.claim_id in {s['claim_id'] for s in support_catalog(allowed)}
    assert '部分' in _render_board(board)


def test_unsupported_legacy_refund_falls_back_without_guessing_state():
    import asyncio
    from application.response_assembly import ResponseAssembler
    result=_verified_order_result()
    fact=replace(result.facts[0],requirement_id='refund.current_state',
        producer_version='refund-view-v1',
        value_json=json.dumps({'order_id':'DP9302','status':'requested','refund_id':'RF1'},
                              sort_keys=True,separators=(',',':')))
    board=_board(replace(result,facts=(fact,)))
    class Composer:
        async def compose(self,payload):
            pytest.fail('unsupported fact must fail before inference')
    answer=asyncio.run(ResponseAssembler(Composer()).assemble(board,current_message='查退款'))
    assert answer.text=='当前退款查询结果格式无法确认，请重新查询。'
