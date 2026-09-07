from dataclasses import replace
import pytest

from application.entity_binding import EntityBindingResolver, BindingStatus
from application.deterministic_resolution import TurnObservations
from application.target_conversation_manager import TargetContextMessage, TargetContextSummary
from tests.test_entity_binding import _state, _context


@pytest.mark.parametrize('wrapper',[('', ''),('订单','现在'),('（','）'),('订单：','，请查询'),(' ',' ')])
@pytest.mark.parametrize('reference',['DP9301','AB-12','AB_123','X'+'9'*64,'DP１２'])
@pytest.mark.parametrize('surface',['current','history','summary'])
def test_supported_references_survive_localized_context_boundaries(wrapper, reference, surface):
    text=wrapper[0]+reference+wrapper[1]
    state=_state()
    context=_context()
    message=text if surface=='current' else '请查询状态'
    if surface=='history':
        context=_context(TargetContextMessage('user',text,'event:1',1))
    if surface=='summary':
        context=replace(context,summary=TargetContextSummary(text,'summary:1',0))
    selected=EntityBindingResolver().resolve(TurnObservations(message),state,context).resolve('reference',state)
    assert selected.status is BindingStatus.UNIQUE
    assert selected.selected.value==reference


@pytest.mark.parametrize('text',['A'*13+'123','DP'+'1'*65,'9DP9301','DP9301Z','_DP9301','DP9301_'])
def test_overlong_or_embedded_identifiers_are_not_partially_bound(text):
    state=_state()
    result=EntityBindingResolver().resolve(TurnObservations('订单'+text+'现在'),state,_context()).resolve('reference',state)
    assert result.status is not BindingStatus.UNIQUE


@pytest.mark.parametrize('order_id', ['DP9301', 'DP9999'])
def test_customer_identifier_is_checked_with_the_whole_answer_and_original_evidence(order_id):
    import asyncio, json
    from application.response_assembly import ResponseAssembler
    from tests.test_response_assembly import _Composer, _board, _verified_order_result
    from tests.test_knowledge_answer_boundary import Verifier
    verifier = Verifier(order_id == 'DP9301')
    answer = '订单' + order_id + '已发货'
    response = asyncio.run(ResponseAssembler(_Composer(answer), knowledge_verifier=verifier).assemble(
        _board(_verified_order_result(order_id='DP9301')), current_message='查询订单DP9301'))
    assert response.verified is (order_id == 'DP9301')
    assert verifier.calls[0][0][1] == answer
    assert json.loads(verifier.calls[0][1]['context'])['facts'][0]['value']['order_id'] == 'DP9301'
