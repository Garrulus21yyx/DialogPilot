from infrastructure.target_model_context import planning_payload_from_request
import gzip
import json
from pathlib import Path
import pytest
from scripts.run_conversation_plan_replay import compile_captured, unclassify_legacy_references


def payload():
    source=Path(__file__).resolve().parents[1]/'artifacts/eval/rag-mixed-business-2026-09-06-v4/mixed-cases.jsonl.gz'
    row=json.loads(gzip.decompress(source.read_bytes()).splitlines()[0])
    return unclassify_legacy_references(planning_payload_from_request(row['api_calls'][0]['request']))


def test_replay_summary_preserves_contextual_query_requirement():
    captured=payload()
    assert captured['conversation_context']['summary']
    captured['conversation_context']['recent_messages']=[]
    with pytest.raises(ValueError,match='requires an explicit resolved query'):
        compile_captured('test',captured,{'status':'resolved','goals':[{'kind':'general_qa'}]})
    result=compile_captured('test',captured,{'status':'resolved','goals':[{'kind':'general_qa','resolved_query':'已发货订单申请改址是否代表改址成功？'}]})
    assert result.commands[0].tool_id=='knowledge_search'


def test_replay_does_not_invent_active_state():
    captured=payload();captured['active_work_controls']=[{'control_id':'unreconstructed'}]
    with pytest.raises(ValueError,match='empty-state'):
        compile_captured('test',captured,{'status':'out_of_scope'})


def test_replay_preserves_ambiguous_binding_resolution_and_explicit_selection():
    captured=payload()
    captured['entity_bindings']=[{'field_name':'reference','status':'AMBIGUOUS','candidates':[
        {'value':value,'source':'CURRENT_MESSAGE','source_ref':f'turn-message:current:reference:{i}'}
        for i,value in enumerate(('DP9301','B20'),1)
    ]}]
    result=compile_captured('test',captured,{'status':'resolved','goals':[
        {'kind':'order_status','order_id':'DP9301','order_id_source_ref':'turn-message:current:reference:1'}
    ]})
    assert result.commands[0].tool_id=='order_lookup'
    captured['entity_bindings'][0]['status']='UNIQUE'
    with pytest.raises(ValueError,match='faithfully'):
        compile_captured('test',captured,{'status':'out_of_scope'})


@pytest.mark.parametrize('status',['STALE','UNAUTHORIZED','MISSING'])
def test_replay_rejects_unreconstructable_binding_state(status):
    captured=payload()
    captured['entity_bindings'][0]['status']=status
    with pytest.raises(ValueError,match='captured bindings'):
        compile_captured('test',captured,{'status':'out_of_scope'})
