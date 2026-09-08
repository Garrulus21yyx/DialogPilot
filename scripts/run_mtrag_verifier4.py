"""Frozen two failures plus supported revisions through current verifier."""
import asyncio,gzip,json,os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from services.answer_verifier import AnswerVerifier
from scripts.run_rag_selected_composition_pair import clean

async def main():
    root=Path('artifacts/eval/rag-g4-verifier4-2026-09-08')
    output=root/'results.jsonl'
    if output.exists():raise ValueError('already started; inspect saved calls rather than rerun')
    inputs=json.loads((root/'inputs.json').read_text())
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
    capture=FrameworkCapture(limit=4);verifier=AnswerVerifier(model,model_profile=profile,callbacks=(capture,))
    for entry in inputs:
        for arm in ('original','repaired'):
            start=len(capture.calls)
            result=await verifier.verify(entry['question'],entry[arm],context=json.dumps(entry['context'],ensure_ascii=False),knowledge_evidence=entry['knowledge_evidence'],agent_outcomes=entry['agent_outcomes'])
            row={'case_id':entry['case_id'],'arm':arm,'answer':entry[arm],'result':asdict(result),'publishable':result.publishable,'calls':clean(capture.calls[start:])}
            with output.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
            print(entry['case_id'],arm,result.status.value,result.reason_code.value,result.publishable,flush=True)
    (root/'results.jsonl.gz').write_bytes(gzip.compress(output.read_bytes(),mtime=0))
    (root/'run.json').write_text(json.dumps({'api_calls':len(capture.calls),'profile':profile.to_dict(),'scope':'current verifier only; no answer regeneration, production publication or prompt change'},indent=2)+'\n')

if __name__=='__main__':asyncio.run(main())
