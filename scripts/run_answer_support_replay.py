"""Rejudge frozen rendered composition answers; no regeneration or publication."""
import argparse
import asyncio
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from services.answer_verifier import AnswerVerifier
from scripts.run_rag_tool_calibration import CaptureClient


async def run(args):
    raw=args.capture.read_bytes()
    if args.capture.suffix=='.gz': raw=gzip.decompress(raw)
    rows=list(map(json.loads,raw.splitlines()))
    if not rows or any(not r.get('rendered') or not r.get('input') for r in rows):
        raise ValueError('expected frozen rendered answers and original composition input')
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
    policy=ModelPolicy.from_env(values)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'manifest.json').write_text(json.dumps({'scope':__doc__,'cases':len(rows),
        'max_api_calls':len(rows),'source_sha256':hashlib.sha256(raw).hexdigest(),
        'profile':policy.profile(ModelRole.VERIFIER).to_dict()},indent=2)+'\n')
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url: options['base_url']=policy.base_url
    async with AsyncAnthropic(**options) as transport:
        client=CaptureClient(transport,limit=len(rows))
        verifier=AnswerVerifier(client=client,model_profile=policy.profile(ModelRole.VERIFIER))
        for row in rows:
            p=row['input'];claims=p['allowed_claims'];packs=[c['value'] for c in claims if c['kind']=='KNOWLEDGE_FACT']
            before=len(client.calls)
            verdict=await verifier.verify(p['current_message'],row['rendered'],
                context=json.dumps({'facts':[c['value'] for c in claims if c['kind']=='FACT'],
                                    'receipts':[c['value'] for c in claims if c['kind']=='RECEIPT'],
                                    **({'user_context':p['conversation_context']} if p.get('conversation_context') is not None else {})},ensure_ascii=False),
                knowledge_evidence={'packs':packs,'allowed_evidence_ids':sorted({e['evidence_id'] for p in packs for e in p['evidence']})},
                agent_outcomes=[{'status':o['status'],'reason':o['reason_code']} for o in p['work_item_outcomes']])
            result={'case_id':row['case_id'],'answer':row['rendered'],'previous_verdict':row.get('verdict'),
                    'verdict':{**asdict(verdict),'status':verdict.status.value,'reason_code':verdict.reason_code.value,
                               'publishable':verdict.publishable},'api_calls':client.calls[before:]}
            with (args.output/'cases.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False,default=str)+'\n')
            print(row['case_id'],verdict.status.value,verdict.reason,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    asyncio.run(run(p.parse_args()))
