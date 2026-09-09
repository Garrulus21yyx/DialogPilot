"""Replay only current-arm visible evidence into the existing answer generator; no gold read."""
import argparse,asyncio,gzip,hashlib,json,os,time,subprocess
from dataclasses import asdict
from pathlib import Path
from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelRole
from mcp.grounded_answer_generator import GroundedAnswerGenerator
from mcp.context_packer import ContextCandidate
from scripts.run_rag_tool_calibration import CaptureClient

async def run(output):
    output.mkdir(parents=True,exist_ok=False)
    input_path=Path('data/eval/ecommerce-complex-v2/heldout.inputs.json')
    capture=Path('artifacts/eval/ecommerce-complex-v2-scope-pair120-v4/runtime/pure-cases.jsonl.gz')
    cases=json.loads(input_path.read_text())
    rows={r['id']:r for r in map(json.loads,gzip.decompress(capture.read_bytes()).splitlines()) if r['arm']=='scope_filtered'}
    assert len(cases)==80 and all(c['id'] in rows for c in cases)
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
    values.update(MODEL_PROVIDER='deepseek',MODEL_SYNTHESIS='deepseek-v4-flash',MODEL_SYNTHESIS_REASONING='none',MODEL_SYNTHESIS_MIN_COMPLETION_TOKENS='0')
    policy=ModelPolicy.from_env(values);profile=policy.profile(ModelRole.SYNTHESIS)
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url:options['base_url']=policy.base_url
    source_paths=[input_path,capture,Path(__file__),Path('mcp/grounded_answer_generator.py'),Path('core/provider_context_budget.py')]
    (output/'manifest.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'model_profile':profile.to_dict(),'scope':'one current retrieval arm, generation replay only; no reference/gold passed to model','api_cap':156,'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}},indent=2)+'\n')
    async with AsyncAnthropic(**options) as transport:
        client=CaptureClient(transport,limit=156);generator=GroundedAnswerGenerator(client,profile)
        for case in cases:
            retrieved=rows[case['id']];wire=retrieved['wire'];before=len(client.calls);start=time.perf_counter()
            assert case['message']==retrieved['original'] and case['history']==retrieved['history']
            contexts=tuple(ContextCandidate(chunk_id=e['evidence_id'],document_id=e['source']['source_id'],text=e['text'],start_char=e['source']['start_char'],end_char=e['source']['end_char'],title=e.get('title',''),applicability=tuple(e['source'].get('applicability',{}).items())) for e in wire.get('evidence',[]))
            answer=None
            if retrieved['result']['status']=='OK':
                answer=asdict(await generator.generate(case['message'],contexts,history=[f'{role}: {text}' for role,text in case['history']]))
            row={'id':case['id'],'question':case['message'],'history':case['history'],'retrieval_status':retrieved['result']['status'],'wire':wire,'answer':answer,'calls':client.calls[before:],'latency_ms':(time.perf_counter()-start)*1000}
            with (output/'answers.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
            print(case['id'],answer['reason'] if answer else 'upstream_failure',flush=True)
        usage={}
        for call in client.calls:
            for k,v in call.get('response',{}).get('usage',{}).items():
                if isinstance(v,(int,float)):usage[k]=usage.get(k,0)+v
        (output/'completion.json').write_text(json.dumps({'cases':80,'api_calls':len(client.calls),'usage':usage,'semantic_accuracy':'not yet assessed'},indent=2)+'\n')
    (output/'answers.jsonl.gz').write_bytes(gzip.compress((output/'answers.jsonl').read_bytes(),mtime=0))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();asyncio.run(run(a.output))
