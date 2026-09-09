"""Positive controls for the same defects-first schema/instruction."""
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
from concise import SYSTEM

ROOT = Path(__file__).parent

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    model = framework_model(policy.profile(ModelRole.VERIFIER), {'api_key': values['ANTHROPIC_API_KEY'],
                            'base_url': policy.base_url}, max_tokens=4096)
    sources = json.loads((ROOT/'source.json').read_text())
    cases = [('state', 1, 'Which state should I use for the new address?'),
        ('prepared_only', 0, 'Your default account address change is prepared but has not been executed. It will use the new address details you supplied, with city New York, state NY, ZIP 10001, USA. Do you approve this account address change? The shipping address change for order #W9911714 is not prepared and is not included in this approval.'),
        ('limitation', 1, 'Neither address has been changed. I still need the state for the new address.'),
        ('choice', 1, 'Is New York the city as well as the state for the address you provided?')]
    for name, index, answer in cases:
        row = sources[index]
        request = json.loads(row['messages'][1]['content'])
        request['answer'] = answer
        schema = copy.deepcopy(row['messages'][2]['content']['function']['parameters']['properties']['result'])
        order = ['issues', 'supported', 'answered', 'approval_terms_complete']
        schema['properties'] = {key: schema['properties'][key] for key in order}
        schema['required'] = order
        capture = FrameworkCapture(limit=1)
        result = {'id': name, 'answer': answer}
        try:
            async with asyncio.timeout(90):
                result['output'] = await structured_call(model, name='submit_claim_checks', schema=schema,
                    system=SYSTEM+'\nWrite issues first: list actual defects in this answer, not recommendations for later execution. Then derive supported/answered/approval_terms_complete from that assessment.',
                    messages=[HumanMessage(json.dumps(request))], callbacks=(capture,))
        except Exception as exc:
            result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['calls'] = capture.calls
        with (ROOT/'defects_controls.jsonl').open('a') as stream:
            stream.write(json.dumps(result, default=str)+'\n')
        print(name, result.get('output', result.get('error')), flush=True)

if __name__ == '__main__': asyncio.run(main())
