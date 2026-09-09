"""Original logged requests, same judge, explicit reasoning counterfactual."""
import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole, ReasoningEffort
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture

ROOT = Path(__file__).parent

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = replace(policy.profile(ModelRole.VERIFIER), reasoning=ReasoningEffort.HIGH,
                      min_completion_tokens=4096)
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'],
                            'base_url': policy.base_url}, max_tokens=8192)
    for row in json.loads((ROOT/'source.json').read_text()):
        capture = FrameworkCapture(limit=1)
        result = {'id': row['observation_id'], 'profile': profile.to_dict()}
        try:
            async with asyncio.timeout(120):
                result['output'] = await structured_call(model, name='submit_claim_checks',
                    schema=row['messages'][2]['content']['function']['parameters']['properties']['result'],
                    system=row['messages'][0]['content'],
                    messages=[HumanMessage(row['messages'][1]['content'])], callbacks=(capture,))
        except Exception as exc:
            result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['calls'] = capture.calls
        with (ROOT/'reasoning.jsonl').open('a') as stream:
            stream.write(json.dumps(result, default=str)+'\n')
        print(result['id'], result.get('output', result.get('error')), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
