"""Replay captured composition inputs only; no planning, retrieval, or business execution."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from scripts.run_rag_tool_calibration import CaptureClient


async def run(args):
    raw = gzip.decompress(args.capture.read_bytes()) if args.capture.suffix == '.gz' else args.capture.read_bytes()
    inputs = []
    for line in raw.splitlines():
        row = json.loads(line)
        for call in row['api_calls']:
            request = call['request']
            if not str(request.get('system', '')).startswith('Compose one concise'):
                continue
            payload = json.loads(request['messages'][0]['content'])
            inputs.append((row['case_id'], payload))
    if not inputs:
        raise ValueError('no captured composition inputs')
    args.output.mkdir(parents=True, exist_ok=False)
    values = {k:str(v) for k,v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    options = dict(api_key=values['ANTHROPIC_API_KEY'], max_retries=0, timeout=60)
    if policy.base_url:
        options['base_url'] = policy.base_url
    manifest = {'scope':__doc__, 'cases':len(inputs),'source_sha256':hashlib.sha256(raw).hexdigest(),
                'synthesis_profile':policy.profile(ModelRole.SYNTHESIS).to_dict()}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    async with AsyncAnthropic(**options) as transport:
        client = CaptureClient(transport,limit=len(inputs))
        provider = AnthropicConversationPlanningProvider(client,model_profile=policy.profile(ModelRole.INTENT),
                                                        synthesis_profile=policy.profile(ModelRole.SYNTHESIS))
        for key,payload in inputs:
            before = len(client.calls)
            try:
                value = await provider.compose(payload)
                result = {'output':value,'error':None}
            except Exception as error:
                result = {'output':None,'error':type(error).__name__}
            row = {'case_id':key,'input':payload,**result,'api_calls':client.calls[before:]}
            with (args.output/'cases.jsonl').open('a') as stream:
                stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
            print(key,result['error'] or 'structured output',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
