"""Replay one captured domain decision with/without working history; no tools execute.

The history-free arm is a diagnostic ablation, NOT a lossless context projection.
"""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage

from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole, ReasoningEffort
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_domain_outcome import SCHEMA, SYSTEM


ASSESSMENT_BASIS_SCHEMA = {**SCHEMA,
    'properties': {
        'policy_basis': {'type': 'string', 'description': 'Brief applicable prerequisites and resulting business state, or explicitly insufficient policy evidence.'},
        'goal_impact': {'type': 'string', 'description': 'Brief impact on the remaining assigned requests, including unknown or not applicable.'},
        **SCHEMA['properties'],
    },
    'required': ['policy_basis', 'goal_impact', *SCHEMA['required']],
}


def remove_actor_text(payload):
    """Keep observations and tool calls; isolate only actor prose, not evidence."""
    payload = json.loads(json.dumps(payload))
    for message in payload['working_context']:
        if message['role'] == 'ai':
            content = message['content']
            message['content'] = [block for block in content if block.get('type') != 'text'] if isinstance(content, list) else ''
    return payload


def remove_runtime_advice(payload):
    """Keep the tool non-execution fact; isolate subsequent scheduler advice."""
    payload = json.loads(json.dumps(payload))
    fact = 'No calls in this batch were executed.'
    for message in payload['working_context']:
        if message['role'] == 'tool' and isinstance(message['content'], str) and message['content'].startswith(fact):
            message['content'] = fact
    return payload


async def run(args):
    values = {**dotenv_values('.env'), **os.environ}
    env = {k: str(v) for k, v in values.items() if v is not None}
    env['LANGFUSE_HOST'] = env.get('LANGFUSE_BASE_URL', '')
    result = subprocess.run(['npx', '--yes', 'langfuse-cli', 'api', 'observations', 'list',
        '--session-id', args.session, '--name', 'ChatAnthropic',
        '--from-start-time', args.start, '--to-start-time', args.end,
        '--fields', 'core,basic,io', '--limit', '100', '--json'], env=env, capture_output=True, text=True, check=True)
    observations = json.loads(result.stdout)['body']['data']
    source, = [r for r in observations if r['id'] == args.observation]
    messages = json.loads(source['input']) if isinstance(source['input'], str) else source['input']
    system, = [m['content'] for m in messages if m['role'] == 'system']
    content, = [m['content'] for m in messages if m['role'] == 'user']
    if system != SYSTEM:
        raise ValueError('captured assessment system differs from production; migration must be explicit')
    original = json.loads(content)
    if original['proposed_outcome'] != 'PREPARE_ACTION':
        raise ValueError('this diagnostic covers action selection only')
    policy = ModelPolicy.from_env(values)
    base = policy.profile(ModelRole.VERIFIER)
    profiles = [replace(base, reasoning=ReasoningEffort(effort), min_completion_tokens=args.max_tokens)
                for effort in args.reasoning]
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'source.json').write_text(json.dumps(source, ensure_ascii=False) + '\n')
    (args.output / 'manifest.json').write_text(json.dumps({
        'scope': __doc__, 'source_observation': args.observation,
        'source_input_sha256': hashlib.sha256(content.encode()).hexdigest(),
        'system_sha256': hashlib.sha256(system.encode()).hexdigest(),
        'profiles': [p.to_dict() for p in profiles], 'repeats': 2,
        'max_api_calls': 4 * len(profiles), 'max_tokens': args.max_tokens, 'timeout_seconds': args.timeout, 'business_tools_enabled': False,
        'ablation': args.ablation,
        'expected_accepted': False, 'interpretation': 'development diagnostic, not held-out closure',
    }, indent=2) + '\n')
    for profile in profiles:
        model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'],
            'base_url': policy.base_url}, max_tokens=args.max_tokens)
        for history in (True, False):
            payload = original if history else (
                original if args.ablation == 'assessment_basis' else
                remove_actor_text(original) if args.ablation == 'actor_text' else
                remove_runtime_advice(original) if args.ablation == 'runtime_advice' else
                {**original, 'working_context': []})
            for repeat in range(2):
                capture = FrameworkCapture(limit=1)
                row = {'history': history, 'reasoning': profile.reasoning.value, 'repeat': repeat}
                try:
                    async with asyncio.timeout(args.timeout):
                        row['assessment'] = await structured_call(model, name='assess_domain_outcome',
                            schema=ASSESSMENT_BASIS_SCHEMA if not history and args.ablation == 'assessment_basis' else SCHEMA,
                            system=system,
                            messages=[HumanMessage(content if history else json.dumps(payload, ensure_ascii=False))],
                            callbacks=(capture,))
                except Exception as exc:
                    row.update(error=type(exc).__name__, detail=str(exc))
                row['calls'] = capture.calls
                with (args.output / 'results.jsonl').open('a') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
                print(profile.reasoning.value, history, repeat, row.get('assessment', row.get('error')), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('session', 'observation', 'start', 'end'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ablation', choices=('working_context', 'actor_text', 'runtime_advice', 'assessment_basis'), default='working_context')
    parser.add_argument('--reasoning', nargs='+', choices=('none', 'high'), default=['none', 'high'])
    parser.add_argument('--max-tokens', type=int, choices=(4096, 8192), default=4096)
    parser.add_argument('--timeout', type=int, choices=(90, 180), default=90)
    asyncio.run(run(parser.parse_args()))
