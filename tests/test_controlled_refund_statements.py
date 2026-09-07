import copy
from dataclasses import asdict, replace
import itertools
import json
import pytest
from jsonschema import Draft202012Validator
from services.customer_operation_views import refund_lookup_statements
from services.customer_operations import RefundStatus
from application.response_assembly import AllowedClaim, _allowed_claims, _render_board
from tests.test_response_assembly import _verified_order_result, _board


def claims():
    return (AllowedClaim('refund', 'CONTROLLED_REFUND_FACT',
        {'lookup_status':'NO_APPLICATION','order_id':'DP9302','order_version':1}, ('refund_status',)),)


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
    assert [c.kind for c in allowed]==['WORK_ITEM_OUTCOME', 'FACT']
    assert '仍有权' not in _render_board(board)
    assert '本次查询未发现' in _render_board(board)
    assert '无法从这份查询记录确认' in _render_board(board)
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
    assert 'summary' not in outcome.value
    assert 'render_mode' not in outcome.value
    assert '部分' in _render_board(board)


def test_unsupported_observation_safe_renderer_never_guesses_state():
    result=_verified_order_result()
    fact=replace(result.facts[0],requirement_id='refund.current_state',
        producer_version='refund-view-v1',
        value_json=json.dumps({'order_id':'DP9302','status':'requested','refund_id':'RF1'},
                              sort_keys=True,separators=(',',':')))
    assert _render_board(_board(replace(result,facts=(fact,)))) == '当前退款查询结果格式无法确认，请重新查询。'
