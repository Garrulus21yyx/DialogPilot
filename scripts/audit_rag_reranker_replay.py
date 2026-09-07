"""Recompute saved evidence coverage without database, model, or API calls."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from core.input_security import UntrustedContentGuard


def audit(original, replay, dataset):
    ds = RagDataset.load(dataset, verify_checksum=True)
    cases = {c.case_id: c for c in ds.cases}
    documents = {d.document_id: d.content for d in ds.documents}
    def load(root):
        return [json.loads(line) for line in gzip.decompress((root/'cases.jsonl.gz').read_bytes()).splitlines()]
    before, after = load(original), load(replay)
    prior = {(r['arm'],r['case_id']): r for r in before}
    assert len(prior) == len(before) == len(after)
    assert set(prior) == {(r['arm'],r['case_id']) for r in after}
    counts, released = {}, []
    for row in after:
        old = prior[row['arm'],row['case_id']]
        keys = ('chunk_id','source_id','source_revision','source_checksum','source_start_char','source_end_char','content','title','ranks','score')
        assert [[r[k] for k in keys] for r in old['candidates']] == [[r[k] for k in keys] for r in row['candidates']]
        assert old['query'] == row['query'] and old['ordered_ids'] == row['ordered_ids']
        assert set(row['ordered_ids']) == {r['chunk_id'] for r in row['candidates']}
        case = cases[row['case_id']]
        def complete(spans):
            return all(any(doc==g.document_id and start<=g.start_char and end>=g.end_char
                           for doc,start,end in spans) for g in case.evidence)
        candidate = {r['chunk_id']:(r['source_id'],r['source_start_char'],r['source_end_char']) for r in row['candidates']}
        pack = row['handler_result']['evidence_pack']
        visible = json.loads(row['tool_message'])
        assert not UntrustedContentGuard().analyze(row['tool_message']).blocked
        def spans(items, source_key):
            result=[]
            for item in items:
                ref=item[source_key]; doc,start,end=ref['source_id'],ref['start_char'],ref['end_char']
                assert item['text'].strip() == documents[doc][start:end].strip()
                result.append((doc,start,end))
            return result
        computed = dict(candidate_complete=complete(candidate.values()),
                        top5_complete=complete([candidate[i] for i in row['ordered_ids'][:5]]),
                        packed_complete=complete(spans(pack['items'],'source_ref')),
                        visible_complete=complete(spans(visible['evidence'],'source')))
        assert len(pack['items']) <= 5
        for key,value in computed.items():
            assert row[key] == value, (row['case_id'],key)
        hits={key:dict(document_id=v[0],source_start_char=v[1],source_end_char=v[2]) for key,v in candidate.items()}
        assert evaluate_ranked_hits(case,row['ordered_ids'],hits,top_k=5) == row['metrics5']
        group=counts.setdefault(row['arm'],dict(cases=0,**{k:0 for k in computed}))
        group['cases']+=1
        for key,value in computed.items():group[key]+=int(value)
        if old['status']=='untrusted_output':
            assert row['status']=='OK'
            released.append(dict(arm=row['arm'],case_id=row['case_id']))
    summary=json.loads((replay/'summary.json').read_text())
    for name,values in counts.items():
        assert all(summary['arms'][name][k]==v for k,v in values.items())
    return dict(api_calls=0,rows=len(after),current_guard_verified=True,verified=counts,released_tool_calls=released,
                artifact_sha256={str(root/'cases.jsonl.gz'):hashlib.sha256((root/'cases.jsonl.gz').read_bytes()).hexdigest() for root in (original,replay)},
                limitations=['original packed_complete measured post-guard data, not original pre-guard pack',
                             'saved replay latency excludes model scoring', 'no final answer evaluation'])


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('original','replay','dataset','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args()
    result=audit(args.original,args.replay,args.dataset)
    with args.output.open('x') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))
