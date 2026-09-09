"""Same output algebra; inspect defects before categorical verdicts."""
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
from concise import SYSTEM

ROOT = Path(__file__).parent

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    model = framework_model(policy.profile(ModelRole.VERIFIER), {'api_key': values['ANTHROPIC_API_KEY'],
                            'base_url': policy.base_url}, max_tokens=4096)
    for row in json.loads((ROOT/'source.json').read_text()):
        schema = row['messages'][2]['content']['function']['parameters']['properties']['result']
        order = ['issues', 'supported', 'answered', 'approval_terms_complete']
        schema['properties'] = {key: schema['properties'][key] for key in order}
        schema['required'] = order
        capture = FrameworkCapture(limit=1)
        result = {'id': row['observation_id']}
        try:
            async with asyncio.timeout(90):
                result['output'] = await structured_call(model, name='submit_claim_checks', schema=schema,
                    system=SYSTEM+'\nWrite issues first: list actual defects in this answer, not recommendations for later execution. Then derive supported/answered/approval_terms_complete from that assessment.',
                    messages=[HumanMessage(row['messages'][1]['content'])], callbacks=(capture,))
        except Exception as exc:
            result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['calls'] = capture.calls
        with (ROOT/'defects_first.jsonl').open('a') as stream:
            stream.write(json.dumps(result, default=str)+'\n')
        print(result['id'], result.get('output', result.get('error')), flush=True)

if __name__ == '__main__': asyncio.run(main())
