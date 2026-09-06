import gzip
import json
from pathlib import Path
import pytest
from scripts.run_conversation_plan_replay import compile_captured


def payload():
    source=Path(__file__).resolve().parents[1]/'artifacts/eval/rag-mixed-business-2026-09-06-v4/mixed-cases.jsonl.gz'
    row=json.loads(gzip.decompress(source.read_bytes()).splitlines()[0])
    return json.loads(row['api_calls'][0]['request']['messages'][0]['content'])


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
