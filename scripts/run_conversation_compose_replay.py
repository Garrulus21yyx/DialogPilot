"""Replay captured composition inputs only; no planning, retrieval, or business execution."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from scripts.run_rag_tool_calibration import CaptureClient
from application.composition_output import render_composition
from application.response_assembly import AllowedClaim, ResponseAssembler
from services.answer_verifier import AnswerVerifier


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
            # Explicit migration of frozen v1 evaluation inputs to the new owner
            # contract. Runtime producers already supply KNOWLEDGE_FACT.
            if payload['schema_version'] == 'conversation-compose-request-v1':
                for claim in payload['allowed_claims']:
                    value=claim['value']
                    if (claim['kind']=='FACT' and isinstance(value,dict)
                            and value.get('status')=='OK' and isinstance(value.get('evidence'),list)):
                        claim['kind']='KNOWLEDGE_FACT'
                payload['schema_version']='conversation-compose-request-v2-segments'
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
                'synthesis_profile':policy.profile(ModelRole.SYNTHESIS).to_dict(),
                'verify':args.verify,'max_api_calls':len(inputs)*(2 if args.verify else 1),
                'input_migration':'v1 FACT evidence views become KNOWLEDGE_FACT; request schema v2',
                'limitation':'component replay; no live source revalidation, business execution or publication'}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    async with AsyncAnthropic(**options) as transport:
        client = CaptureClient(transport,limit=len(inputs)*(2 if args.verify else 1))
        verifier = AnswerVerifier(client=client,model_profile=policy.profile(ModelRole.VERIFIER)) if args.verify else None
        provider = AnthropicConversationPlanningProvider(client,model_profile=policy.profile(ModelRole.INTENT),
                                                        synthesis_profile=policy.profile(ModelRole.SYNTHESIS))
        for key,payload in inputs:
            before = len(client.calls)
            try:
                value = await provider.compose(payload)
                claims=tuple(AllowedClaim(c['claim_id'],c['kind'],c['value'],tuple(c['source_refs'])) for c in payload['allowed_claims'])
                text,used=render_composition(value,claims)
                text,used=ResponseAssembler.prepare_composed_response(text,used,claims,payload['current_message'],payload['work_item_outcomes'])
                result = {'output':value,'rendered':text,'used_claim_ids':used,'error':None}
                if verifier:
                    packs=[c.value for c in claims if c.kind=='KNOWLEDGE_FACT']
                    verdict=await verifier.verify(payload['current_message'],text,
                        context=json.dumps({'facts':[c.value for c in claims if c.kind=='FACT'],
                                            'receipts':[c.value for c in claims if c.kind=='RECEIPT']},ensure_ascii=False),
                        knowledge_evidence={'packs':packs,'allowed_evidence_ids':sorted({e['evidence_id'] for p in packs for e in p['evidence']})},
                        agent_outcomes=[{'status':o['status'],'reason':o['reason_code']} for o in payload['work_item_outcomes']])
                    result['verdict']={**asdict(verdict),'status':verdict.status.value,'reason_code':verdict.reason_code.value,'publishable':verdict.publishable}
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
    parser.add_argument('--verify',action='store_true',help='Check rendered answers against captured evidence; no live source validation.')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
