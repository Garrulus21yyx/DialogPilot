"""Frozen two failures plus supported revisions through current verifier."""
import argparse,asyncio,gzip,json,os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from services.answer_verifier import AnswerVerifier
from scripts.run_rag_selected_composition_pair import clean

SEMANTIC_FOCUS = """
For each factual conclusion, check that its supporting passage concerns the same exact
entity, product/form/model variant, jurisdiction and time scope. Similar names or shared
words do not authorize transferring a rule between entities. Preserve the evidence's
conditions and logical strength: a necessary condition is not sufficient eligibility,
and unspecified qualification does not establish a particular threshold or requirement.
Check added modifiers and quantifiers separately from the supported part of a sentence.
If the supplied original evidence cannot establish a conclusion, set supported=false
and identify the unsupported addition. A qualified hypothesis or question is not an
assertion of an event; accurately expressing a lack of evidence can be supported.
"""

async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--inputs',type=Path)
    parser.add_argument('--semantic-focus',action='store_true')
    parser.add_argument('--model',choices=['deepseek-v4-flash','deepseek-v4-pro'],default='deepseek-v4-flash')
    args=parser.parse_args()
    source=Path('artifacts/eval/rag-g4-verifier4-2026-09-08')
    root=args.output or source
    root.mkdir(parents=True,exist_ok=True)
    output=root/'results.jsonl'
    if output.exists():raise ValueError('already started; inspect saved calls rather than rerun')
    input_path=args.inputs or source/'inputs.json'
    inputs=json.loads(input_path.read_text())
    import services.claim_verification as claims
    if args.semantic_focus:claims.SYSTEM += SEMANTIC_FOCUS
    (root/'experiment.json').write_text(json.dumps({'added_instruction':SEMANTIC_FOCUS if args.semantic_focus else '', 'input_source':str(input_path)},indent=2)+'\n')
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    profile=ModelProfile(args.model,ReasoningEffort.NONE,'deepseek')
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
    (root/'run.json').write_text(json.dumps({'api_calls':len(capture.calls),'profile':profile.to_dict(),'scope':'verifier experiment only; no production change','semantic_focus':args.semantic_focus},indent=2)+'\n')

if __name__=='__main__':asyncio.run(main())
