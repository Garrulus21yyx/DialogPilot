"""Frozen verifier requests: original/repaired answers, no retrieval or generation."""
import argparse
import asyncio
import gzip
import json
import os
import re
from dataclasses import replace
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelProfile, ReasoningEffort
from core.framework_models import framework_model
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture
from scripts.run_rag_selected_composition_pair import clean

FOCUS = '''
Check the proposition being answered, including short affirmative or negative openings.
A yes/no opening must agree with the question's proposition and the answer's later conclusion;
correct details do not repair a contradictory opening. Judge the whole reply in context.
Check the scope and quantifiers of each policy claim. Evidence about specified subtypes or
examples does not establish a rule for all broader categories. Preserve conditions and exceptions.
'''


async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--focused',action='store_true');parser.add_argument('--clear-controls',action='store_true');args=parser.parse_args()
    args.output.mkdir(exist_ok=False,parents=True)
    frozen=json.loads(gzip.decompress(Path('artifacts/eval/rag-g2-regression6-clean-2026-09-07/semantic-witnesses.json.gz').read_bytes()))
    values={**dotenv_values('.env'),**os.environ};policy=ModelPolicy.from_env(values)
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=4096)
    capture=FrameworkCapture(limit=4);rows=[]
    for witness in frozen:
        request=witness['verifier_input'];payload=json.loads(request['messages'][0]['content'])
        original=payload['answer']
        if witness['case_id']=='ellipsis-shipping':
            assert original.startswith('是的，');repaired=original.removeprefix('是的，')
        else:
            old='按消费者指定尺寸或要求制作的定制商品均不支持无理由退货'
            assert old in original
            repaired=original.replace(old,'刻字定制商品和按消费者指定尺寸制作的定制商品不适用无理由退货')
        if args.clear_controls:
            cite=re.search(r'\[E[a-zA-Z0-9]+\]',original).group(0)
            if witness['case_id']=='ellipsis-shipping':
                original='加急寄回运费也在报销范围内，可以按标准运费上限报销。'+cite
                repaired='加急运费不在报销范围内；确认属于质量问题的标准运费可按规定报销，上限十八元。'+cite
            else:
                original='刻字定制商品适用七天无理由退货，即使已经拆封也可以。'+cite
                repaired='刻字定制商品不适用七天无理由退货，即使未拆封且签收不足七日也不适用。'+cite
        schema=request['tools'][0]['input_schema']['properties']['result']
        for arm,answer in [('original',original),('repaired',repaired)]:
            submitted={**payload,'answer':answer};before=len(capture.calls)
            output=await structured_call(model,name='submit_claim_checks',schema=schema,system=request['system']+(FOCUS if args.focused else ''),content=json.dumps(submitted,ensure_ascii=False),callbacks=(capture,))
            row=dict(case_id=witness['case_id'],arm=('contradicted_control' if arm=='original' else 'supported_control') if args.clear_controls else arm,answer=answer,output=output,calls=clean(capture.calls[before:]))
            rows.append(row)
            (args.output/'rows.json.gz').write_bytes(gzip.compress(json.dumps(rows,ensure_ascii=False).encode(),mtime=0))
            print(witness['case_id'],arm,output,flush=True)
    (args.output/'report.json').write_text(json.dumps(dict(api_calls=len(capture.calls),focused=args.focused,clear_controls=args.clear_controls,added_instruction=FOCUS if args.focused else '',profile=profile.to_dict(),scope='frozen original verifier schema/evidence; not full current runtime',results=[{k:v for k,v in r.items() if k!='calls'} for r in rows]),ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':asyncio.run(main())
