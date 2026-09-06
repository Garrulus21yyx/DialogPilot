import json
import pytest
from evaluation.rag_mixed_report import report


def test_visible_source_is_measured_from_actual_model_output_not_internal_artifact(tmp_path):
    definition={'case_id':'one','expected_tools':['order_lookup','knowledge_search'],'expected_source_id':'policy'}
    definitions=tmp_path/'definitions.json';definitions.write_text(json.dumps([definition]))
    capture=tmp_path/'capture.jsonl'
    row={'case_id':'one','outcome_type':'Completed','outcome':{'response':{
        'response':'政策说明 [E123]','synthesis_reason':'KNOWLEDGE_SUPPORT_CHECKED'}},
        'tools':[{'name':'order_lookup','result':{}},{'name':'knowledge_search','result':{
            'data':{'evidence':[{'source':{'source_id':'policy'}}]},'output_for_model':'{"evidence":[]}'}}],
        'api_calls':[]}
    capture.write_text(json.dumps(row)+'\n')
    value=report(capture,definitions)
    assert value['complete_capture']
    assert value['counts']['expected_tool_coverage']==1
    assert value['counts']['expected_source_visible']==0
    assert value['counts']['expected_source_cited']==0
    assert value['counts']['knowledge_support_checked']==1
    row['tools'][1]['result']['output_for_model']=json.dumps({'evidence':[{'source':{'source_id':'policy'},'evidence_id':'E123'}]})
    capture.write_text(json.dumps(row)+'\n')
    assert report(capture,definitions)['counts']['expected_source_cited']==1
    capture.write_text((json.dumps(row)+'\n')*2)
    with pytest.raises(ValueError,match='duplicate'):
        report(capture,definitions)
