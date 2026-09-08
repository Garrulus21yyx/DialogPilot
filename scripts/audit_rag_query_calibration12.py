"""Recompute all-case and searched-subset metrics from preserved wire evidence."""
import gzip, hashlib, json
from pathlib import Path
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from infrastructure.target_model_context import planning_payload_from_request
from tempfile import TemporaryDirectory

ROOT = Path('artifacts/eval/rag-query-calibration12-2026-09-08')

def read_rows(path):
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).decode().splitlines()]

def main():
    global ROOT
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=ROOT)
    ROOT=parser.parse_args().root
    manifest = json.loads((ROOT/'manifest.json').read_text())
    snapshot = json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory() as temp:
        for name,text in snapshot.items(): Path(temp,name).write_text(text)
        ds = RagDataset.load(Path(temp),verify_checksum=True)
    cases = {c.case_id:c for c in ds.select_cases('dev')}
    queries = read_rows(ROOT/'queries.jsonl.gz')
    retrieval = read_rows(ROOT/'retrieval.jsonl.gz')
    assert len(queries)==12 and len({cases[q['case_id']].group_id for q in queries})==12
    assert [q['case_id'] for q in queries]==[c['case_id'] for c in manifest['cases']]
    docs = {d.document_id:d for d in ds.documents}
    roles = json.loads((ROOT/'history-roles.json').read_text())
    records=[]
    for q in queries:
        c=cases[q['case_id']];assert q['raw_query']==c.query
        assert len(q['calls'])==1
        payload=planning_payload_from_request(q['calls'][0]['request'])
        assert payload['message']==c.query
        history=payload['conversation_context']['recent_messages']
        assert [(h['role'],h['content']) for h in history]==list(zip(roles[c.case_id],c.history))
        record={'case_id':c.case_id,'raw_query':c.query,'planning_disposition':q['proposal']['disposition'],
                'resolved_queries':q['resolved_queries'],'response_text':q['proposal']['response_text'],'arms':{}}
        for arm in ('raw','resolved'):
            matches=[r for r in retrieval if r['case_id']==c.case_id and r['arm']==arm]
            if not matches:
                assert arm=='resolved' and len(q['resolved_queries'])!=1
                record['arms'][arm]={'searched':False,'candidate_complete':False,'visible_complete':False,'mrr':0.,'ndcg':0.}
                continue
            r,=matches
            assert r['query']==(c.query if arm=='raw' else q['resolved_queries'][0])
            assert len(r['pool'])<=20 and r['packed_tokens']<=2600
            items=r['tool_message'].get('evidence',[]);assert len(items)<=5
            hitmap={}
            for i,e in enumerate(items):
                ref=e['source'];doc=docs[ref['source_id']]
                assert ref['checksum']==hashlib.sha256(doc.content.encode()).hexdigest()
                assert e['text']==doc.content[ref['start_char']:ref['end_char']]
                hitmap[str(i)]={'document_id':ref['source_id'],'source_start_char':ref['start_char'],'source_end_char':ref['end_char']}
            m=evaluate_ranked_hits(c,list(hitmap),hitmap,top_k=5)
            visible=all(any(h['document_id']==g.document_id and h['source_start_char']<=g.start_char and h['source_end_char']>=g.end_char for h in hitmap.values()) for g in c.evidence)
            record['arms'][arm]={'searched':True,'candidate_complete':r['complete20'],'visible_complete':visible,'mrr':m['mrr'],'ndcg':m['ndcg'],'metrics':m}
        records.append(record)
    summary={'scope':'12 exposed dev multi-turn cases, source-order selection concentrated in DMV; 100 document corpus; real planner and local retrieval/CE/production pack-wire replay, not PG or final answer accuracy',
             'n':12,'api_calls':sum(len(q['calls']) for q in queries),'arms':{},'paired':{},'rows':records,
             'current_source_hashes_match':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in manifest['source_hashes'].items()}}
    for arm in ('raw','resolved'):
        summary['arms'][arm]={key:sum(r['arms'][arm][key] for r in records) for key in ('searched','candidate_complete','visible_complete')}
        summary['arms'][arm].update({key:sum(r['arms'][arm][key] for r in records)/12 for key in ('mrr','ndcg')})
    for stage in ('candidate_complete','visible_complete'):
        summary['paired'][stage]={'rescued':sum(not r['arms']['raw'][stage] and r['arms']['resolved'][stage] for r in records),
                                 'lost':sum(r['arms']['raw'][stage] and not r['arms']['resolved'][stage] for r in records)}
    summary['limits']=['No retrieval is zero evidence delivery here, not automatically wrong answer or wrong clarification.',
                       'Raw short-query backend replay is a diagnostic comparator, not an old Agent system baseline.',
                       'Respond may be appropriate; author semantic review required, not model PASS or field matching.',
                       'No adoption or recall-gain claim from this run.']
    (ROOT/'audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='rows'},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
