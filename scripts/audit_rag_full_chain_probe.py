"""Replay provenance, citation and explicit development evidence checks without APIs.

These checks measure source visibility and transport outcomes, not semantic answer
accuracy. Manual semantic review is recorded separately in the accompanying report.
"""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
import re
from pathlib import Path
from statistics import median

from application.knowledge_tool_contract import evidence_id, evidence_items, model_evidence, validate_answer_citations
from evaluation.rag_ecommerce_dev import synthetic_development
from evaluation.rag_applicability_dev import applicability_development

REQUIRED_EVIDENCE = {
    'opened-negation': ('已拆封耳机非质量原因不适用无理由退货',),
    'ellipsis-shipping': ('加急运费不在报销范围内', '十八元'),
    'historical-policy': ('十二元',),
    'eu-scope-fresh': ('欧洲区未拆封普通商品', '十四日'),
    'custom-exception-fresh': ('定制', '不适用'),
    'effective-boundary-fresh': ('十八元',),
}


def audit(path: Path):
    docs = (*synthetic_development()[0], *applicability_development()[0])
    originals = {(d.document_id,hashlib.sha256(d.content.encode()).hexdigest()):d.content for d in docs}
    rows=[]
    raw = (path/'full-cases.jsonl').read_text() if (path/'full-cases.jsonl').exists() else gzip.decompress((path/'full-cases.jsonl.gz').read_bytes()).decode()
    for line in raw.splitlines():
        case=json.loads(line); response=case.get('outcome',{}).get('response',{})
        answer=response.get('response',''); items=[]; model_texts=[]; provenance=True
        for tool in case.get('tools',[]):
            if tool['name'] != 'knowledge_search':continue
            wire=tool['result']['data']
            if wire.get('status')!='OK':continue
            items.extend(evidence_items(wire))
            visible = json.loads(tool['result']['output_for_model']) if tool['result'].get('output_for_model') else model_evidence(wire)
            model_texts.extend(e['text'] for e in visible['evidence'])
        for item in items:
            ref=item['source_ref']; original=originals.get((ref['source_id'],ref['checksum']))
            provenance &= original is not None and original[ref['start_char']:ref['end_char']]==item['text']
        allowed={evidence_id(item['chunk_id']) for item in items}
        cited=set(re.findall(r'\[(E[a-zA-Z0-9]+)\]',answer))
        try:
            validate_answer_citations(answer,allowed);citation_format_valid=True
        except ValueError:
            citation_format_valid=False
        required=REQUIRED_EVIDENCE[case['case_id']]
        rows.append({'case_id':case['case_id'],'runtime_verified':bool(response.get('verified')),
            'evidence_source_exact':bool(items) and provenance,
            'packed_required_quotes_visible':all(any(q in i['text'] for i in items) for q in required),
            'model_view_required_quotes_visible':all(any(q in text for text in model_texts) for q in required),
            'citation_applicable':bool(items),'citation_format_valid':citation_format_valid,
            'visible_citations_valid':bool(cited) and cited <= allowed and citation_format_valid,
            'api_calls':len(case['api_calls']),'latency_ms':response.get('latency_ms'),
            'answer':answer})
    summary={'cases':len(rows),'api_calls':sum(r['api_calls'] for r in rows),
        'latency_p50_ms':median(r['latency_ms'] for r in rows if r['latency_ms'] is not None),
        **{key:sum(r[key] for r in rows) for key in ('runtime_verified','evidence_source_exact','packed_required_quotes_visible','model_view_required_quotes_visible','visible_citations_valid','citation_applicable','citation_format_valid')},
        'limits':'Synthetic development cases; required quote checks are not benchmark Recall/nDCG or semantic accuracy. Model view uses saved output_for_model when present, otherwise replayed serialization; no claim about later provider input coverage.',
        'rows':rows}
    (path/'independent-audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return {k:v for k,v in summary.items() if k!='rows'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory',type=Path)
    print(json.dumps(audit(p.parse_args().directory),ensure_ascii=False,indent=2))
