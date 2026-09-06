from dataclasses import replace

import pytest

from application.conversation_agent import ConversationAgent
from application.deterministic_resolution import TurnObservations
from application.entity_binding import BindingSource, BindingStatus, EntityBindingError, EntityBindingResolver
from infrastructure.postgres_target_runtime import _binding_from_payload, _binding_to_payload
from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
from tests.test_entity_binding import _context, _state


@pytest.mark.parametrize('identifier', ['DP9301', 'B20', 'E17', 'SKU_883', 'XY-12345'])
@pytest.mark.parametrize('context', ['订单{}当前状态', '商品型号{}安装条件', '错误码{}是什么意思'])
def test_lexical_occurrence_never_proves_business_type(identifier, context):
    state = _state()
    bindings = EntityBindingResolver().resolve(TurnObservations(context.format(identifier)), state, _context())
    assert bindings.resolve('order_id', state).status is BindingStatus.MISSING
    reference = bindings.resolve('reference', state).selected
    assert reference.value == identifier
    assert reference.type_selection is None
    assert ConversationAgent._select_binding(bindings, 'order_id', '', '', state) is None
    with pytest.raises(ValueError):
        ConversationAgent._select_binding(bindings, 'order_id', identifier, '', state)
    selected = ConversationAgent._select_binding(bindings, 'order_id', identifier, reference.source_ref, state)
    assert selected.value_json == reference.value_json
    assert selected.source_ref == reference.source_ref
    assert selected.source == reference.source
    assert selected.type_selection == 'conversation-agent-reference-selection-v1'
    assert selected.field_name == 'order_id'
    assert _binding_from_payload(_binding_to_payload(selected)) == selected
    codec = target_checkpoint_serializer()
    assert codec.loads_typed(codec.dumps_typed(selected)) == selected


def test_type_choice_cannot_change_value_or_cross_scope():
    state = _state()
    bindings = EntityBindingResolver().resolve(TurnObservations('DP9301 B20'), state, _context())
    first, second = bindings.bindings
    with pytest.raises(ValueError):
        ConversationAgent._select_binding(bindings, 'order_id', first.value, second.source_ref, state)
    with pytest.raises(ValueError):
        ConversationAgent._select_binding(bindings, 'order_id', first.value, first.source_ref, replace(state,user_id='other'))


def test_legacy_text_binding_is_not_upgraded_during_restore():
    state = _state()
    reference = EntityBindingResolver().resolve(TurnObservations('DP9301'), state, _context()).bindings[0]
    wire = _binding_to_payload(reference.select_type('order_id'))
    del wire['type_selection']
    with pytest.raises(EntityBindingError, match='explicit type selection'):
        _binding_from_payload(wire)


def test_structured_input_retains_declared_type_without_semantic_inference():
    state = _state()
    bindings = EntityBindingResolver().resolve(
        TurnObservations('B20', (('order_id','DP9301'),)), state, _context())
    selected = bindings.resolve('order_id', state).selected
    assert selected.source is BindingSource.STRUCTURED_INPUT
    assert selected.type_selection is None
    assert selected.value == 'DP9301'
    assert bindings.resolve('reference',state).selected.value == 'B20'


def test_workstream_inheritance_keeps_type_selection_and_fingerprint():
    from application.conversation_state import WorkstreamState, WorkstreamStatus
    from application.work_item import ArgumentValue
    state = _state()
    binding = EntityBindingResolver().resolve(TurnObservations('DP9301'),state,_context()).bindings[0].select_type('order_id')
    stream = WorkstreamState('ws','order_logistics','order_status','READY',WorkstreamStatus.ACTIVE,1,
        slots=(ArgumentValue.create('order_id','DP9301'),),slot_bindings=(binding,))
    state = replace(state,workstreams=(stream,))
    inherited = EntityBindingResolver().resolve(TurnObservations('继续'),state,_context()).resolve('order_id',state).selected
    assert inherited.type_selection == binding.type_selection
    assert inherited.source is BindingSource.WORKSTREAM_SLOT
    unselected = replace(binding,source=BindingSource.STRUCTURED_INPUT,type_selection=None)
    assert state.fingerprint != replace(state,workstreams=(replace(stream,slot_bindings=(unselected,)),)).fingerprint


@pytest.mark.parametrize('nest', [lambda x:x, lambda x:[x], lambda x:{'optional':x}, lambda x:{'outer':[{'binding':x}]}])
def test_legacy_checkpoint_failure_is_typed_even_when_nested(nest):
    from infrastructure.langgraph_checkpoint import TargetCheckpointContractError
    state=_state()
    binding=EntityBindingResolver().resolve(TurnObservations('DP9301'),state,_context()).bindings[0].select_type('order_id')
    # Construct only the obsolete serialized fixture, bypassing today's owner.
    object.__setattr__(binding,'type_selection',None)
    codec=target_checkpoint_serializer()
    encoded=codec.dumps_typed(nest(binding))
    with pytest.raises(TargetCheckpointContractError):
        codec.loads_typed(encoded)


@pytest.mark.parametrize('identity', [['application.entity_binding','EntityBinding'], ['application','entity_binding','EntityBinding']])
def test_checkpoint_json_keeps_plain_data_and_rejects_target_constructor(identity):
    import json
    from infrastructure.langgraph_checkpoint import TargetCheckpointContractError
    codec=target_checkpoint_serializer()
    plain={'answer':None,'facts':[]}
    assert codec.loads_typed(('json',json.dumps(plain).encode())) == plain
    legacy={'optional':{'lc':2,'type':'constructor','id':identity,'kwargs':{}}}
    with pytest.raises(TargetCheckpointContractError):
        codec.loads_typed(('json',json.dumps(legacy).encode()))
