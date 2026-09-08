"""Four bounded Flash compositions from frozen parent validation evidence."""
import asyncio,gzip,hashlib,json,os
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from scripts.run_mtrag_flash_answers import ModelCapture
from scripts.replay_mtrag_direct_input import replay
from scripts.run_rag_selected_composition_pair import clean
from scripts.adapt_mtrag_retrieval_dataset import digest
ROOT=Path('artifacts/eval/rag-g4-parent-answers2-2026-09-08')
async def main():
    ids={'e1b602e47ded79a35d8df4eefe194e39<::>1','fdee20f7fd677e420742b09989623d68<::>6'}
    source=Path('artifacts/eval/rag-g4-cloud-parent-validation8-2026-09-08/pack/cases.json.gz')
    rows=[r for r in json.load(gzip.open(source,'rt')) if r['case_id'] in ids];assert len(rows)==2
    inputs={}
    for line in Path('/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl').open():
        r=json.loads(line)
        if r['task_id'] in ids:
            turns=r['input'];assert turns[-1]['speaker']=='user'
            inputs[r['task_id']]={'current_message':turns[-1]['text'],'conversation_context':{'recent_messages':[{'role':'assistant' if t['speaker']=='agent' else t['speaker'],'content':t['text']} for t in turns[:-1]]}}
    assert set(inputs)==ids
    ROOT.mkdir(exist_ok=False)
    fixture={'rows':rows,'inputs':inputs,'pack_sha256':digest(source)}
    (ROOT/'inputs.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n')
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    model=ModelCapture(framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=800));model.calls=FrameworkCapture(limit=4)
    def save(row):
        row['calls']=clean(model.calls.calls[-1:])
        with (ROOT/'answers.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(row['case_id'],row['arm'],row['answer'],flush=True)
    await replay(rows,model=model,inputs=inputs,on_record=save)
    (ROOT/'answers.jsonl.gz').write_bytes(gzip.compress((ROOT/'answers.jsonl').read_bytes(),mtime=0))
    (ROOT/'run.json').write_text(json.dumps({'calls':len(model.calls.calls),'profile':profile.to_dict(),'fixture_sha256':digest(ROOT/'inputs.json'),'scope':'compose only, reconstructed knowledge Fact; no full Agent/verifier/publication'},indent=2)+'\n')
if __name__=='__main__':asyncio.run(main())
