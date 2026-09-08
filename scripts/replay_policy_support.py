"""Counterfactual policy visibility on a frozen reply; no business execution.

Existing snapshot transport has separate integration tests. This probe isolates
policy text visibility, not registry reconstruction or end-to-end delivery.
"""
import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture
from services.claim_verification import verify_claims


async def run(args):
    values = {**dotenv_values('.env'), **os.environ}
    env = {k: str(v) for k, v in values.items() if v is not None}
    env['LANGFUSE_HOST'] = env.get('LANGFUSE_BASE_URL', '')
    response = subprocess.run(['npx', '--yes', 'langfuse-cli', 'api', 'observations', 'list',
        '--session-id', args.session, '--name', 'ChatAnthropic',
        '--from-start-time', args.start, '--to-start-time', args.end,
        '--fields', 'core,basic,io', '--limit', '100', '--json'],
        env=env, capture_output=True, text=True, check=True)
    source, = [r for r in json.loads(response.stdout)['body']['data'] if r['id'] == args.observation]
    messages = json.loads(source['input'])
    original = json.loads(next(m['content'] for m in messages if m['role'] == 'user'))
    policy_source = json.loads(args.policy_source.read_text())
    policy_messages = json.loads(policy_source['input'])
    domain_input = json.loads(next(m['content'] for m in policy_messages if m['role'] == 'user'))
    policy = ModelPolicy.from_env(values)
    profile = policy.profile(ModelRole.VERIFIER)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'manifest.json').write_text(json.dumps({
        'scope': __doc__, 'source': source, 'policy_source_id': policy_source['id'],
        'policy_text': domain_input['business_policy'], 'profile': profile.to_dict(),
        'max_api_calls': 4, 'max_tokens': 4096, 'business_tools_enabled': False,
    }, ensure_ascii=False) + '\n')
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'],
        'base_url': policy.base_url}, max_tokens=4096)
    for include_policy in (False, True):
        request = json.loads(json.dumps(original))
        if include_policy:
            request['evidence']['context']['capability_policy'] = {
                'source_observation': policy_source['id'],
                'agents': [{'agent_id': 'retail', 'description': domain_input['business_policy']}],
            }
        for repeat in range(2):
            capture = FrameworkCapture(limit=1)
            row = {'include_policy': include_policy, 'repeat': repeat}
            try:
                async with asyncio.timeout(90):
                    row['assessment'] = asdict(await verify_claims(model, profile,
                        **request, callbacks=(capture,)))
            except Exception as exc:
                row.update(error=type(exc).__name__, detail=str(exc))
            row['calls'] = capture.calls
            with (args.output / 'results.jsonl').open('a') as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            print(include_policy, repeat, row.get('assessment', row.get('error')), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('session', 'observation', 'start', 'end'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--policy-source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
