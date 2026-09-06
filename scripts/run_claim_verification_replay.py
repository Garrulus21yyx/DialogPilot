"""Frozen answer/fact replay for the candidate atomic verification contract."""
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
from services.claim_verification import verify_claims
from scripts.run_rag_tool_calibration import CaptureClient


async def run(args):
    raw=args.capture.read_bytes()
    if args.capture.suffix == '.gz':raw=gzip.decompress(raw)
    rows=list(map(json.loads,raw.splitlines()))
    if not rows or len({r['case_id'] for r in rows})!=len(rows):
        raise ValueError('unique nonempty cases required')
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy=ModelPolicy.from_env(values);profile=policy.profile(ModelRole.VERIFIER)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'manifest.json').write_text(json.dumps({
        'scope':__doc__,'source_sha256':hashlib.sha256(raw).hexdigest(),
        'cases':len(rows),'max_api_calls':len(rows),'profile':profile.to_dict(),
        'max_tokens':4096,'production_publication_enabled':False},indent=2)+'\n')
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url:options['base_url']=policy.base_url
    async with AsyncAnthropic(**options) as transport:
        client=CaptureClient(transport,limit=len(rows))
        for row in rows:
            p=row['input'];claims=p['allowed_claims']
            # Match the evidence content of the baseline replay, including context.
            packs=[c['value'] for c in claims if c['kind']=='KNOWLEDGE_FACT']
            evidence={'context':{'facts':[c['value'] for c in claims if c['kind'] in ('FACT', 'CONTROLLED_REFUND_FACT')],
                        'receipts':[c['value'] for c in claims if c['kind']=='RECEIPT'],
                        **({'user_context':p['conversation_context']} if p.get('conversation_context') is not None else {})},
                      'knowledge_evidence':{'packs':packs,'allowed_evidence_ids':sorted({
                          e['evidence_id'] for pack in packs for e in pack['evidence']})},
                      'agent_outcomes':[{'status':o['status'],'reason':o['reason_code']} for o in p['work_item_outcomes']]}
            before=len(client.calls)
            result={'case_id':row['case_id'],'error':None,'all_supported':False}
            try:
                assessment=await verify_claims(client,profile,question=p['current_message'],
                    answer=row['rendered'],evidence=evidence)
                result.update(assessment=asdict(assessment),all_supported=assessment.all_supported)
            except Exception as exc:
                result.update(error=type(exc).__name__,error_message=str(exc))
            result['api_calls']=client.calls[before:]
            with (args.output/'cases.jsonl').open('a') as f:
                f.write(json.dumps(result,ensure_ascii=False,default=str)+'\n')
            print(row['case_id'],result['error'] or result['all_supported'],flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    asyncio.run(run(p.parse_args()))
