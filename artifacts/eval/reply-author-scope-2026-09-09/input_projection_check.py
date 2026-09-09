"""One production-provider input check, without executing any proposed work."""
import asyncio
import json
import os
from pathlib import Path
from dotenv import dotenv_values
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.INTENT)
    model = framework_model(profile, {'api_key':values['ANTHROPIC_API_KEY'], 'base_url':policy.base_url}, max_tokens=4096)
    capture = FrameworkCapture(limit=1)
    provider = AnthropicConversationPlanningProvider({ModelRole.INTENT:model}, model_profile=profile,
        synthesis_profile=profile, max_tokens=4096, callbacks=(capture,))
    payload = {'message':'Blue, please.', 'supported_goals':['general_qa','delegate_task'],
        'domain_capabilities':[{'agent_id':'retail','description':'Retail orders and changes'}],
        'pending_input':{'requested_fields':[{'target_work_item_id':'w1','field_name':'color','value_schema':'string'}],
                         'objectives':[{'work_item_id':'w1','objective':'Exchange the selected item'}]}}
    record = {'scope':'production provider; no business execution', 'input':payload}
    try:
        async with asyncio.timeout(120):
            record['output'] = await provider.plan(payload)
    except Exception as exc:
        record['error'] = {'type':type(exc).__name__, 'message':str(exc)}
    record['calls'] = capture.calls
    Path(__file__).with_name('input_projection_check.json').write_text(json.dumps(record,ensure_ascii=False,indent=2,default=str))
    print(json.dumps(record.get('output',record.get('error')),ensure_ascii=False))

if __name__=='__main__':
    asyncio.run(main())
