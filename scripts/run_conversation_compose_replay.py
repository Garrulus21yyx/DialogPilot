"""Replay native-text composition captures only; never rerun business tools."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from core.framework_models import conversation_models, framework_model
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from services.answer_verifier import AnswerVerifier


async def run(args):
    raw = gzip.decompress(args.capture.read_bytes()) if args.capture.suffix == '.gz' else args.capture.read_bytes()
    inputs = []
    for line in raw.splitlines():
        row = json.loads(line)
        for call in row['api_calls']:
            request = call['request']
            if not str(request.get('system', '')).startswith('You are the customer-facing conversation agent.'):
                continue
            payload = json.loads(request['messages'][0]['content'])
            if payload.get('schema_version') != 'conversation-compose-request-v5-text':
                raise ValueError('historical composition captures must be replayed at their recorded commit')
            if not args.case_ids or row['case_id'] in args.case_ids:
                inputs.append((row['case_id'], payload))
    if not inputs:
        raise ValueError('no native-text composition captures; historical artifacts remain unchanged')
    args.output.mkdir(parents=True, exist_ok=False)
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    config = {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url}
    capture = FrameworkCapture(limit=len(inputs) * (2 if args.verify else 1))
    provider = AnthropicConversationPlanningProvider(conversation_models(policy, config),
        model_profile=policy.profile(ModelRole.INTENT), synthesis_profile=policy.profile(ModelRole.SYNTHESIS),
        callbacks=(capture,))
    verifier = AnswerVerifier(framework_model(policy.profile(ModelRole.VERIFIER), config, max_tokens=4096),
        model_profile=policy.profile(ModelRole.VERIFIER), callbacks=(capture,))
    (args.output / 'manifest.json').write_text(json.dumps({
        'scope': __doc__, 'source_sha256': hashlib.sha256(raw).hexdigest(), 'cases': len(inputs),
        'schema_version': 'conversation-compose-request-v5-text', 'verify': args.verify,
        'limitation': 'component replay, no live source validation or business/publication execution'}, indent=2))
    for key, payload in inputs:
        before = len(capture.calls)
        result = {'case_id': key, 'input': payload, 'error': None}
        try:
            text = await provider.compose(payload)
            result['output'] = text
            if args.verify:
                evidence = payload['evidence']
                packs = [f['value'] for f in evidence['facts'] if f['requirement_id'] == 'knowledge.active_source']
                verdict = await verifier.verify(payload['current_message'], text,
                    context=json.dumps(evidence, ensure_ascii=False),
                    knowledge_evidence={'packs': packs},
                    agent_outcomes=evidence['outcomes'])
                result['verdict'] = asdict(verdict)
        except Exception as exc:
            from core.tracing import exception_chain
            result['error'] = exception_chain(exc)
        result['api_calls'] = capture.calls[before:]
        with (args.output / 'cases.jsonl').open('a') as stream:
            stream.write(json.dumps(result, ensure_ascii=False, default=str) + '\n')
        print(key, result['error'] or 'reply generated', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--case-ids', nargs='*')
    asyncio.run(run(parser.parse_args()))
