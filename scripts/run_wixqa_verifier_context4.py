"""Frozen verifier context ablation, preserving every knowledge pack verbatim."""
import asyncio,gzip,json,os,hashlib
from pathlib import Path
from dataclasses import asdict
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from services.claim_verification import verify_claims
from scripts.run_rag_selected_composition_pair import clean

async def main():
    root=Path('artifacts/eval/wixqa-verifier-context4-2026-09-08');root.mkdir(parents=True,exist_ok=False)
    source=Path('artifacts/eval/wixqa-real-entry-next2-2026-09-08/0.5/full-cases.jsonl.gz')
    row=json.loads(gzip.decompress(source.read_bytes()).decode().splitlines()[0])
    request=json.loads(row['api_calls'][-1]['request']['messages'][0]['content'])
    full=request['evidence'];compact={'approval_required':False,'knowledge_evidence':full['knowledge_evidence']}
    assert not full['approval_required']
    repaired=request['answer'].split('\n\n')[0]
    inputs=[{'context_mode':mode,'answer_mode':answer_mode,'question':request['question'],'answer':answer,'evidence':evidence} for mode,evidence in [('full',full),('knowledge_only',compact)] for answer_mode,answer in [('original',request['answer']),('core_only',repaired)]]
    (root/'inputs.json.gz').write_bytes(gzip.compress(json.dumps(inputs,ensure_ascii=False).encode(),mtime=0))
    (root/'manifest.json').write_text(json.dumps({'gap':'R06/R10','hypothesis':'Unneeded duplicated runtime context may affect semantic support; compare full vs knowledge-only, preserving all original packs','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'budget':4,'model':'deepseek-v4-flash','scope':'selected consumed diagnostic; compact is not a production contract for mixed business turns','adoption':'No production adoption from single case; report support independently of answered. No guessing entity equivalence or editing sources.'},indent=2)+'\n')
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url,'max_retries':0},max_tokens=4096)
    capture=FrameworkCapture(limit=4)
    for entry in inputs:
        before=len(capture.calls)
        assessment=await verify_claims(model,profile,question=entry['question'],answer=entry['answer'],evidence=entry['evidence'],callbacks=(capture,))
        result={'context_mode':entry['context_mode'],'answer_mode':entry['answer_mode'],'assessment':asdict(assessment),'calls':clean(capture.calls[before:])}
        with (root/'results.jsonl').open('a') as f:f.write(json.dumps(result,ensure_ascii=False)+'\n')
        print(entry['context_mode'],entry['answer_mode'],assessment.supported,assessment.issues,flush=True)
    (root/'results.jsonl.gz').write_bytes(gzip.compress((root/'results.jsonl').read_bytes(),mtime=0))
if __name__=='__main__':asyncio.run(main())
