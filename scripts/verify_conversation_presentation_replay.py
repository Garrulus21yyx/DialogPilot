"""Semantic verification of native-text captures against their original evidence.

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
from core.model_policy import ModelPolicy, ModelRole
from services.answer_verifier import AnswerVerifier
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture


async def run(args):
    raw=gzip.decompress(args.capture.read_bytes()) if args.capture.suffix=='.gz' else args.capture.read_bytes()
    rows=[json.loads(line) for line in raw.splitlines()]
    if not rows or any('input' not in row or 'output' not in row for row in rows):
        raise ValueError('expected captured composition replay rows')
    if any(row['input'].get('schema_version') != 'conversation-compose-request-v5-text'
           or not isinstance(row['output'], str) for row in rows):
        raise ValueError('historical segmented captures must be replayed at their recorded commit')
    args.output.mkdir(parents=True,exist_ok=False)
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy=ModelPolicy.from_env(values)
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url: options['base_url']=policy.base_url
    manifest={'scope':__doc__,'cases':len(rows),'source_sha256':hashlib.sha256(raw).hexdigest(),
              'max_api_calls':len(rows),'verifier_profile':policy.profile(ModelRole.VERIFIER).to_dict()}
    (args.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    client=FrameworkCapture(limit=len(rows))
    verifier=AnswerVerifier(framework_model(policy.profile(ModelRole.VERIFIER), options, max_tokens=4096),model_profile=policy.profile(ModelRole.VERIFIER), callbacks=(client,))
    for row in rows:
        before=len(client.calls)
        payload,value=row['input'],row['output']
        result={'case_id':row['case_id'],'error':None}
        try:
            evidence=payload['evidence']
            packs=[f['value'] for f in evidence['facts'] if f['requirement_id']=='knowledge.active_source']
            verdict=await verifier.verify(payload['current_message'],value,
                context=json.dumps(evidence,ensure_ascii=False),
                knowledge_evidence={'packs':packs}, agent_outcomes=evidence['outcomes'])
            result.update(answer=value,verdict={**asdict(verdict),'publishable':verdict.publishable})
        except Exception as error:
            from core.tracing import exception_chain
            result['error']=exception_chain(error)
        result['api_calls']=client.calls[before:]
        with (args.output/'cases.jsonl').open('a') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,default=str)+'\n')
        print(row['case_id'],result['error'] or result['verdict']['status'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__=='__main__':
    main()
