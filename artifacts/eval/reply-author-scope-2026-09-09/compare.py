"""Author-only A/B, logged redacted evidence, no tools bound or executed."""
import asyncio
import copy
import json
import os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture

ROOT = Path(__file__).parent

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.SYNTHESIS)
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url}, max_tokens=4096)
    row = next(r for r in json.loads((ROOT/'source.json').read_text()) if r['name']=='compose_response')
    original = json.loads(row['input'][1]['content'])
    async def run(mode):
        payload = copy.deepcopy(original)
        messages = [SystemMessage(row['input'][0]['content'])]
        if mode == 'native_history':
            history = payload['evidence']['user_context'].pop('recent_messages', [])
            payload['evidence']['user_context']['history_provenance'] = [
                {k:v for k,v in item.items() if k!='content'} for item in history]
            for item in history:
                messages.append((AIMessage if item['role']=='assistant' else HumanMessage)(item['content']))
        messages.append(HumanMessage(json.dumps(payload, ensure_ascii=False, sort_keys=True)))
        capture = FrameworkCapture(limit=1)
        record = {'mode': mode, 'source_id':row['id'], 'profile':profile.to_dict(), 'output_budget':4096}
        try:
            async with asyncio.timeout(180):
                result = await model.ainvoke(messages, config={'callbacks':[capture], 'run_name':'author_scope_'+mode})
            record.update(output=result.text, metadata=result.response_metadata)
        except Exception as exc:
            record['error'] = {'type':type(exc).__name__, 'message':str(exc)}
        record['calls'] = capture.calls
        with (ROOT/'comparison.jsonl').open('a') as stream:
            stream.write(json.dumps(record, default=str)+'\n')
        print(json.dumps({k:v for k,v in record.items() if k not in {'calls','profile'}}, default=str), flush=True)
    await asyncio.gather(run('original_json'), run('native_history'))

if __name__ == '__main__':
    asyncio.run(main())
