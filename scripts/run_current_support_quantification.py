"""Recount frozen evidence judgments and evaluate current SDK; no production writes."""
import argparse,asyncio,gzip,hashlib,json,os,statistics,subprocess,time
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.framework_models import framework_model
from core.model_policy import ModelPolicy,ModelRole
from evaluation.framework_capture import FrameworkCapture
from services.claim_verification import verify_claims


def category(result, legacy=False):
    if result.get('error'):return 'protocol_or_api_failure'
    a=result.get('assessment')
    if not a:return 'missing_assessment'
    if legacy:
        checks=a.get('checks',[])
        if not checks:return 'missing_assessment'
        if any(c['verdict'] in ('INSUFFICIENT','CONTRADICTED') for c in checks):return 'support_rejection'
        if not all(c['verdict']=='SUPPORTED' for c in checks):return 'missing_assessment'
    elif not a['supported']:return 'support_rejection'
    if not result['passed']:return 'needs_only_rejection'
    return 'passed'


def totals(rows, legacy=False):
    counts={}
    for r in rows:
        c=category(r['result'],legacy);counts[c]=counts.get(c,0)+1
    bad=[r for r in rows if not r['expected_supported']];good=[r for r in rows if r['expected_supported']]
    return {'cases':len(rows),'categories':counts,'unsupported':len(bad),
      'detected_unsupported':sum(category(r['result'],legacy)=='support_rejection' for r in bad),
      'unsupported_passed':sum(category(r['result'],legacy)=='passed' for r in bad),
      'unsupported_needs_only':sum(category(r['result'],legacy)=='needs_only_rejection' for r in bad),
      'unsupported_protocol_failure':sum(category(r['result'],legacy) in ('protocol_or_api_failure','missing_assessment') for r in bad),
      'supported':len(good),'supported_passed':sum(category(r['result'],legacy)=='passed' for r in good)}


def sanitize(v):
    if isinstance(v,list):return [sanitize(x) for x in v if not isinstance(x,dict) or x.get('type') not in ('thinking','redacted_thinking')]
    if isinstance(v,dict):return {k:sanitize(x) for k,x in v.items() if k not in ('thinking','signature')}
    return v


async def main(args):
    args.output.mkdir(parents=True,exist_ok=False)
    oldraw=args.historical.read_bytes();old=[json.loads(l) for l in gzip.decompress(oldraw).splitlines()]
    recount={arm:totals([{'expected_supported':r['expected_supported'],'result':r['results'][arm]} for r in old],True) for arm in ('A','B')}
    (args.output/'historical-recount.json').write_text(json.dumps(recount,indent=2)+'\n')
    casesraw=args.cases.read_bytes();cases=json.loads(casesraw)
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
    policy=ModelPolicy.from_env(values);profile=policy.profile(ModelRole.VERIFIER)
    assert profile.model=='deepseek-v4-flash' and profile.reasoning.value=='none'
    (args.output/'manifest.json').write_text(json.dumps({'git_head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'cases_sha256':hashlib.sha256(casesraw).hexdigest(),'historical_sha256':hashlib.sha256(oldraw).hexdigest(),'profile':profile.to_dict(),'calls_limit':len(cases),'max_tokens':4096,'note':'consumed20 synthetic cases; current protocol and SDK differ; no tuning/retries'},indent=2)+'\n')
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
    sem=asyncio.Semaphore(2)
    async def one(c):
      async with sem:
        capture=FrameworkCapture(limit=1);t=time.monotonic();r={'passed':False,'error':None}
        try:
            a=await verify_claims(model,profile,question=c['question'],answer=c['answer'],evidence=c['evidence'],callbacks=(capture,))
            r.update(assessment=asdict(a),passed=a.supported and a.answered)
        except Exception as exc:r.update(error=type(exc).__name__,detail=str(exc)[:150])
        r.update(calls=sanitize(capture.calls),latency_ms=(time.monotonic()-t)*1000)
        row={'id':c['id'],'expected_supported':c['supported'],'result':r}
        with (args.output/'current.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
        print(c['id'],category(r),flush=True)
        return row
    rows=await asyncio.gather(*(one(c) for c in cases));s=totals(rows)
    s['api_calls']=sum(len(r['result']['calls']) for r in rows);s['median_latency_ms']=statistics.median(r['result']['latency_ms'] for r in rows)
    s['usage']={k:sum(c.get('usage',{}).get(k,0) for r in rows for c in r['result']['calls']) for k in ('input_tokens','output_tokens','total_tokens')}
    (args.output/'summary.json').write_text(json.dumps(s,indent=2)+'\n')
    (args.output/'current.jsonl.gz').write_bytes(gzip.compress((args.output/'current.jsonl').read_bytes(),mtime=0));print(json.dumps(s),flush=True)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--cases',type=Path,required=True);p.add_argument('--historical',type=Path,required=True);p.add_argument('--output',type=Path,required=True);asyncio.run(main(p.parse_args()))
