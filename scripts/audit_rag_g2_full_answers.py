"""Source/citation audit of the two frozen synthetic full-chain outcomes.

Quote checks are evidence visibility, not automatic semantic answer grading.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
from application.knowledge_tool_contract import evidence_id

REQUIRED={
 'synthetic-g2-opened-nonquality':['已拆封耳机非质量原因不适用无理由退货'],
 'synthetic-g2-hypothetical-arrival':['审核通过后由支付渠道处理款项','提交退款请求与资金到账是不同阶段'],
}

def audit(root):
    manifest=json.loads((root/'manifest.json').read_text())
    docs={(d['document_id'],hashlib.sha256(d['content'].encode()).hexdigest()):d['content'] for d in manifest['source_documents']}
    cases=[json.loads(line) for line in gzip.decompress((root/'full-cases.jsonl.gz').read_bytes()).splitlines()]
    rows=[]
    for case in cases:
        response=case.get('outcome',{}).get('response',{})
        answer=response.get('response','');ids={};visible=[];exact=True
        for tool in case['tools']:
            if tool['name']!='knowledge_search':continue
            result=tool['result'];pack=(result.get('data',{}).get('evidence_pack') or {}).get('items',[])
            for item in pack:
                ref=item['source_ref'];source=docs.get((ref['source_id'],ref['checksum']))
                exact &= source is not None and source[ref['start_char']:ref['end_char']]==item['text']
                ids[evidence_id(item['chunk_id'])]=item
            visible.extend(json.loads(result['output_for_model']).get('evidence',[]))
        for item in visible:
            packed=ids.get(item['evidence_id']);exact &= packed is not None and packed['text']==item['text']
        cited=set(re.findall(r'\[(E[a-zA-Z0-9]+)\]',answer))
        rows.append(dict(case_id=case['case_id'],completed=case.get('outcome_type')=='Completed',runtime_verified=response.get('verified',False),
                         only_knowledge_tools=bool(case['tools']) and all(t['name']=='knowledge_search' for t in case['tools']),
                         exact_source_and_visible_text=bool(visible) and exact,
                         required_evidence_visible=all(any(q in e['text'] for e in visible) for q in REQUIRED[case['case_id']]),
                         citations_valid=bool(cited) and cited<=set(ids),answer=answer,answer_sha256=hashlib.sha256(answer.encode()).hexdigest(),
                         cited_evidence={key:ids[key]['text'] for key in sorted(cited) if key in ids},api_calls=len(case['api_calls'])))
    result=dict(scope='synthetic development; source visibility and citations only; manual semantic review separate',cases=len(rows),rows=rows)
    (root/'source-answer-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    assert all(all(r[k] for k in ('completed','runtime_verified','only_knowledge_tools','exact_source_and_visible_text','required_evidence_visible','citations_valid')) for r in rows)
    print('Audited',len(rows),'complete outputs with exact source text and valid citations')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);audit(p.parse_args().root)
