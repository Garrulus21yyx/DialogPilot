"""Frozen full-context calibration. No tools or public messages execute."""
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
SCOPE = """Assess approval_scope_valid independently of answer coverage and factual support.
It is true when the reply does not solicit execution permission, or solicits it
only for the exact runtime-selected pending_actions. With no selected proposal,
an execution-permission question is invalid. A genuine missing-value or alternative
choice question is valid; if it also requests permission to perform an action,
assess that part independently. Future/queued goals and retained approvals are
not selected proposals. A selected proposal authorizes presenting only its own
action, subject and changes, not the entire multi-action goal. Give specific
repair feedback in issues when false. This check never grants execution permission."""

async def main():
    sources = json.loads((ROOT / 'source.json').read_text())
    cases = []
    for row in sources:
        request = json.loads(row['messages'][1]['content'])
        schema = row['messages'][2]['content']['function']['parameters']['properties']['result']
        cases.append((row['observation_id'], row['messages'][0]['content'], request, schema, False))
    for name, index, answer in [
        ('plain_missing_value', 1, 'Which state should I use for the new address?'),
        ('selected_only', 0, 'The default account address change to 101 Highway, New York, NY 10001, USA is prepared but not executed. Do you approve this account address change? The shipping address change for order #W9911714 is still to be prepared separately.'),
        ('no_permission_request', 1, 'Neither address has been changed. I still need the state for the new address.'),
        ('ordinary_choice', 1, 'Is New York the city as well as the state for the address you provided?'),
    ]:
        _, system, request, schema, _ = cases[index]
        request = copy.deepcopy(request)
        request['answer'] = answer
        cases.append((name, system, request, schema, True))
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.VERIFIER)
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'],
        'base_url': policy.base_url}, max_tokens=4096)
    for key, system, request, schema, valid in cases:
        for variant in ('original', 'scope'):
            output_schema = copy.deepcopy(schema)
            instruction = system
            if variant == 'scope':
                output_schema['required'].append('approval_scope_valid')
                output_schema['properties']['approval_scope_valid'] = {'type': 'boolean', 'description': SCOPE}
                instruction += '\n' + SCOPE
            capture = FrameworkCapture(limit=1)
            result = {'case': key, 'variant': variant, 'expected_scope_valid': valid}
            try:
                async with asyncio.timeout(90):
                    output = await structured_call(model, name='submit_claim_checks', schema=output_schema,
                        system=instruction, messages=[HumanMessage(json.dumps(request, ensure_ascii=False))],
                        callbacks=(capture,))
                result['output'] = output
            except Exception as exc:
                result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
            result['calls'] = capture.calls
            with (ROOT / 'comparison.jsonl').open('a') as stream:
                stream.write(json.dumps(result, ensure_ascii=False, default=str) + '\n')
            print(key, variant, result.get('output', result.get('error')), flush=True)

if __name__ == '__main__':
    asyncio.run(main())
