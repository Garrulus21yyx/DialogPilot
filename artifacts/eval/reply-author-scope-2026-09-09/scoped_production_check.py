"""Replay logged planner context through current provider, without tool execution."""
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import dotenv_values
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_model_context import planning_payload_from_request

async def main():
    root = Path(__file__).parent
    row = next(r for r in json.loads((root/'source.json').read_text()) if r['name']=='conversation_actions')
    payload = planning_payload_from_request({'system':row['input'][0]['content'],
        'messages':[m for m in row['input'] if m['role'] in {'user','assistant'}]})
    payload.pop('business_action_semantics',None)
    # Historical provider excluded this catalog from message content; rebuild
    # declarations from the same captured native tool schemas, never execute them.
    control_names = {'delegate_task','cancel_active_work','continue_active_work','bind_read_goals','review_action','supply_input'}
    captured_names = {m['content']['function']['name'] for m in row['input'] if m['role']=='tool'}
    payload['supported_goals'] = sorted(captured_names & {'delegate_task','cancel_active_work','continue_active_work'})
    payload['atomic_reads'] = [{'owner_agent':'retail','tool_id':m['content']['function']['name'],
        'description':m['content']['function'].get('description',''),
        'input_schema':m['content']['function']['parameters'], 'requirement_ids':[]}
        for m in row['input'] if m['role']=='tool' and m['content']['function']['name'] not in control_names]
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.INTENT)
    model = framework_model(profile, {'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
    capture = FrameworkCapture(limit=1)
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT:model},model_profile=profile,
        synthesis_profile=profile,max_tokens=4096,callbacks=(capture,))
    from application.conversation_actions import planning_actions
    assert {a.name for a in planning_actions(payload)} == captured_names, 'Replay tool availability differs from original'
    record = {'scope':'logged context, reconstructed declaration catalog, current provider; no tools executed'}
    try:
        async with asyncio.timeout(120):
            record['output'] = await provider.plan(payload)
    except Exception as exc:
        record['error']={'type':type(exc).__name__,'message':str(exc)}
    record['calls']=capture.calls
    (root/(sys.argv[1] if len(sys.argv)>1 else 'scoped_production_check.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str))
    print(json.dumps(record.get('output',record.get('error')),ensure_ascii=False))

if __name__=='__main__':
    asyncio.run(main())
