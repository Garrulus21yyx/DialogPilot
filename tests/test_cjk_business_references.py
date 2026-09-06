from dataclasses import replace
import pytest

from application.entity_binding import EntityBindingResolver, BindingStatus
from application.deterministic_resolution import TurnObservations
from application.target_conversation_manager import TargetContextMessage, TargetContextSummary
from tests.test_entity_binding import _state, _context
from application.response_assembly import ResponseAssembler, AllowedClaim


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
    selected=EntityBindingResolver().resolve(TurnObservations(message),state,context).resolve('order_id',state)
    assert selected.status is BindingStatus.UNIQUE
    assert selected.selected.value==reference


@pytest.mark.parametrize('text',['A'*13+'123','DP'+'1'*65,'9DP9301','DP9301Z','_DP9301','DP9301_'])
def test_overlong_or_embedded_identifiers_are_not_partially_bound(text):
    state=_state()
    result=EntityBindingResolver().resolve(TurnObservations('订单'+text+'现在'),state,_context()).resolve('order_id',state)
    assert result.status is not BindingStatus.UNIQUE


def test_publication_detects_invented_identifier_touching_chinese():
    claims=(AllowedClaim('order','fact',{'order_id':'DP9301','status':'shipped'},()),)
    with pytest.raises(ValueError,match='unsupported business reference'):
        ResponseAssembler._verify_composed('订单DP9999已发货',('order',),claims, '查询订单DP9301')
    ResponseAssembler._verify_composed('订单DP9301已发货',('order',),claims, '查询订单DP9301')
