"""Action preparation flags belong to delegation, not direct knowledge goals."""
import jsonschema
import pytest
from application.conversation_agent import planning_output_schema


@pytest.mark.parametrize('flag',[False,True])
def test_delegation_accepts_both_explicit_action_scopes(flag):
    schema=planning_output_schema(['delegate_task','refund_policy'])
    value={'status':'resolved','goals':[{'kind':'delegate_task','target_agent':'billing_refund','objective':'Explain policy' if not flag else 'Prepare requested refund','allow_action_proposals':flag}]}
    jsonschema.validate(value,schema)
    del value['goals'][0]['allow_action_proposals']
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,schema)


@pytest.mark.parametrize('flag',[None,False,True])
def test_direct_knowledge_uses_query_and_omits_delegation_fields(flag):
    schema=planning_output_schema(['delegate_task','refund_policy'])
    goal={'kind':'refund_policy','resolved_query':'Does approval imply arrival?'}
    jsonschema.validate({'status':'resolved','goals':[goal]},schema)
    for field,value in [('allow_action_proposals',flag),('target_agent','billing_refund'),('objective','Explain policy')]:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({'status':'resolved','goals':[{**goal,field:value}]},schema)
