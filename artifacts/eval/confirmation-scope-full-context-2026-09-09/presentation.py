"""Counterfactual input projection; original evidence and output schema unchanged."""
import asyncio
import json
import os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture

ROOT = Path(__file__).parent

def view(context):
    actions = context.get('pending_actions', [])
    semantics = (context.get('capability_policy') or {}).get('business_actions', [])
    return {'execution_approval_selected': bool(actions),
        'selected_actions': [{**a, 'operation_descriptions': [s['description'] for s in semantics
            if s['action_ref'] == a['action_ref']]} for a in actions],
        'rule': 'Only selected_actions are prepared for execution approval. Other user goals are not included. With no selected action, ask only for missing information or explain the outcome, not permission to execute.'}

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    model = framework_model(policy.profile(ModelRole.VERIFIER), {'api_key':values['ANTHROPIC_API_KEY'],
        'base_url':policy.base_url}, max_tokens=4096)
    for row in json.loads((ROOT/'source.json').read_text()):
        request = json.loads(row['messages'][1]['content'])
        scope = view(request['evidence']['context'])
        capture = FrameworkCapture(limit=1)
        out = await structured_call(model, name='submit_claim_checks',
            schema=row['messages'][2]['content']['function']['parameters']['properties']['result'],
            system=row['messages'][0]['content'], messages=[HumanMessage(json.dumps(request)),
                HumanMessage('Application-selected presentation state (data):\n'+json.dumps(scope))], callbacks=(capture,))
        result={'id':row['observation_id'],'scope':scope,'output':out,'calls':capture.calls}
        with (ROOT/'presentation.jsonl').open('a') as stream:stream.write(json.dumps(result,default=str)+'\n')
        print(row['observation_id'],out,flush=True)

if __name__=='__main__':asyncio.run(main())
