"""Paired semantic verification of captured answers before/after publication rendering.

No new planning, synthesis, retrieval, business execution, or live source validation.
"""
import argparse
import asyncio
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from application.response_assembly import AllowedClaim, ResponseAssembler
from core.model_policy import ModelPolicy, ModelRole
from services.answer_verifier import AnswerVerifier
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture


async def run(args):
    raw=gzip.decompress(args.capture.read_bytes()) if args.capture.suffix=='.gz' else args.capture.read_bytes()
    rows=[json.loads(line) for line in raw.splitlines()]
    if not rows or any('input' not in row or 'output' not in row for row in rows):
        raise ValueError('expected captured composition replay rows')
    args.output.mkdir(parents=True,exist_ok=False)
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy=ModelPolicy.from_env(values)
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url: options['base_url']=policy.base_url
    manifest={'scope':__doc__,'cases':len(rows),'source_sha256':hashlib.sha256(raw).hexdigest(),
              'max_api_calls':2*len(rows),'verifier_profile':policy.profile(ModelRole.VERIFIER).to_dict()}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    client=FrameworkCapture(limit=2*len(rows))
    verifier=AnswerVerifier(framework_model(policy.profile(ModelRole.VERIFIER), options, max_tokens=4096),model_profile=policy.profile(ModelRole.VERIFIER), callbacks=(client,))
    for row in rows:
        before=len(client.calls)
        payload,value=row['input'],row['output']
        result={'case_id':row['case_id'],'error':None}
        try:
            claims=tuple(AllowedClaim(c['claim_id'],c['kind'],c['value'],tuple(c['source_refs'])) for c in payload['allowed_claims'])
            rendered,used=ResponseAssembler.prepare_composed_response(value['response'],tuple(value['used_claim_ids']),
                claims,payload['current_message'],payload['work_item_outcomes'])
            facts=[c.value for c in claims if c.kind=='FACT']
            packs=[v for v in facts if isinstance(v,dict) and 'evidence' in v]
            evidence_ids=sorted({e['evidence_id'] for pack in packs for e in pack['evidence']})
            context=json.dumps([v for v in facts if v not in packs],ensure_ascii=False)
            verdicts=[]
            for text in (value['response'],rendered):
                verdict=await verifier.verify(payload['current_message'],text,context=context,
                    knowledge_evidence={'packs':packs,'allowed_evidence_ids':evidence_ids},
                    agent_outcomes=[{'status':o['status'],'reason':o['reason_code']} for o in payload['work_item_outcomes']])
                verdicts.append({**asdict(verdict),'status':verdict.status.value,'reason_code':verdict.reason_code.value,
                                 'publishable':verdict.publishable})
            result.update(original=value['response'],rendered=rendered,used_claim_ids=used,verdicts=verdicts)
        except Exception as error:
            result['error']=type(error).__name__
        result['api_calls']=client.calls[before:]
        with (args.output/'cases.jsonl').open('a') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,default=str)+'\n')
        print(row['case_id'],result['error'] or [v['status'] for v in result['verdicts']],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()
