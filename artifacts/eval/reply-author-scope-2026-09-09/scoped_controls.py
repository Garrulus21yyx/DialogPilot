"""Four preregistered planning controls, without executing proposed tools."""
import asyncio
import json
import os
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage, SystemMessage
from application.conversation_actions import planning_actions, action_proposal
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture
from scoped_main import ROOT, ROLE

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    model = framework_model(policy.profile(ModelRole.INTENT), {'api_key':values['ANTHROPIC_API_KEY'], 'base_url':policy.base_url}, max_tokens=4096)
    rows = [
        ('conversation', 'Thanks, that answers my question. Goodbye.', {}),
        ('read', 'What is the general return policy?', {}),
        ('input', 'Blue, please.', {'pending_input':{'requested_fields':[
            {'target_work_item_id':'w1','field_name':'color','value_schema':'string'}]}}),
        ('approval_question', 'Yes, approve that unchanged account address update. Also, what is the general return policy?',
         {'pending_approval':{'approval_id':'a1', 'action_ref':'modify_user_address:v1',
           'arguments':{'address1':'10 Main Street','city':'Boston','state':'MA','zip':'02108','country':'USA'},
           'presented_to_user':True}}),
    ]
    for name, message, state in rows:
        payload = {'supported_goals':['general_qa','delegate_task'],
            'domain_capabilities':[{'agent_id':'retail','description':'Investigate retail orders and prepare requested business changes.'}], **state}
        actions = planning_actions(payload)
        capture = FrameworkCapture(limit=1)
        record = {'case':name, 'input':message, 'state':state}
        try:
            async with asyncio.timeout(120):
                out = await model.bind_tools([a.tool() for a in actions], tool_choice='auto').ainvoke(
                    [SystemMessage(ROLE),HumanMessage(json.dumps({'current_request':message,'runtime_context':state}))],
                    config={'callbacks':[capture],'run_name':'scoped_control_'+name})
            record.update(text=out.text, tool_calls=out.tool_calls,
                          converted=action_proposal(actions,out.tool_calls,out.text))
        except Exception as exc:
            record['error']={'type':type(exc).__name__,'message':str(exc)}
        record['calls']=capture.calls
        with (ROOT/'scoped_controls.jsonl').open('a') as stream:
            stream.write(json.dumps(record,default=str)+'\n')
        print(json.dumps({k:v for k,v in record.items() if k!='calls'},default=str),flush=True)

if __name__=='__main__':
    asyncio.run(main())
