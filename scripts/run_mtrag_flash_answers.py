"""Frozen 8-case, 16-call composition diagnostic; no upstream Agent or verifier."""
import asyncio,gzip,hashlib,json,os
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ModelRole,ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from scripts.replay_mtrag_direct_input import replay
from scripts.run_rag_selected_composition_pair import clean

ROOT=Path('artifacts/eval/rag-g4-mtrag-answer8-2026-09-08')

class ModelCapture:
    def __init__(self,model):self.model=model;self.calls=FrameworkCapture(limit=16)
    async def ainvoke(self,messages,**kwargs):
        self.messages=messages
        return await self.model.ainvoke(messages,config={'callbacks':[self.calls],'run_name':'mtrag_composition_pair'})

async def main():
    fixture=json.loads((ROOT/'inputs.json').read_text())
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    model=ModelCapture(framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=800))
    output=ROOT/'answers.jsonl'
    if output.exists():raise ValueError('existing answers; do not overwrite or rerun')
    def save(row):
        row['calls']=clean(model.calls.calls[-1:])
        with output.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(row['case_id'],row['arm'],flush=True)
    await replay(fixture['rows'],model=model,inputs=fixture['inputs'],on_record=save)
    (ROOT/'answers.jsonl.gz').write_bytes(gzip.compress(output.read_bytes(),mtime=0))
    (ROOT/'run.json').write_text(json.dumps({'api_calls':len(model.calls.calls),'profile':profile.to_dict(),'inputs_sha256':hashlib.sha256((ROOT/'inputs.json').read_bytes()).hexdigest(),'scope':'compose only; original history, frozen retrieved evidence; no verifier/publication'},indent=2)+'\n')

if __name__=='__main__':asyncio.run(main())
