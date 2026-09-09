"""Authority-separated snapshot diagnostic; no business execution."""
import asyncio
import copy
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

def project(request):
    request = copy.deepcopy(request)
    evidence = request['evidence']
    context = evidence['context']
    for name in ('agent_outcomes', 'task_plan', 'coverage'):
        evidence.pop(name, None)
    context['user_context'].pop('turn_execution', None)
    for outcome in context['outcomes']:
        outcome['requested_objective'] = outcome.pop('objective', None)
        outcome['observed_segment'] = {key: outcome.pop(key) for key in ('status', 'reason_code')}
        outcome['execution_feedback'] = [f for f in outcome['execution_feedback']
                                         if f.get('accepted') is not True]
        outcome['approval_scope'] = 'Only context.pending_actions identify prepared operations; the requested objective is not the prepared scope.'
    return request

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    model = framework_model(policy.profile(ModelRole.VERIFIER), {'api_key': values['ANTHROPIC_API_KEY'],
                            'base_url': policy.base_url}, max_tokens=4096)
    for row in json.loads((ROOT/'source.json').read_text()):
        capture = FrameworkCapture(limit=1)
        result = {'id': row['observation_id']}
        try:
            async with asyncio.timeout(90):
                result['output'] = await structured_call(model, name='submit_claim_checks',
                    schema=row['messages'][2]['content']['function']['parameters']['properties']['result'],
                    system=row['messages'][0]['content'],
                    messages=[HumanMessage(json.dumps(project(json.loads(row['messages'][1]['content']))))], callbacks=(capture,))
        except Exception as exc:
            result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['calls'] = capture.calls
        with (ROOT/'projection.jsonl').open('a') as stream:
            stream.write(json.dumps(result, default=str)+'\n')
        print(result['id'], result.get('output', result.get('error')), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
