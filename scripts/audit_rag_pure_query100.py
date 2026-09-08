"""Verify all query inputs and ranked evidence without new model calls."""
import json,hashlib
from pathlib import Path
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from scripts.audit_rag_three_followups import checked_scores,verify
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.doc2dial_history_roles import history_roles
from evaluation.rag_provider_free import projection,complete
from mcp.rank_fusion import fuse_rankings
OUT=Path('artifacts/eval/rag-pure-query100-2026-09-08')
def main():
    manifest=read(OUT/'manifest.json');ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True);roles=history_roles('/tmp/doc2dial_v1.0.1.zip',ds.cases,member='doc2dial_dial_test.json');caps=read(OUT/'captures.jsonl',True);results=read(OUT/'retrieval.json')
    assert len(caps)==len(results)==100 and [c.case_id for c in ds.cases]==manifest['ids']
    assert digest(ROOT/'doc2dial/cases.jsonl')==manifest['source_sha256']
    reused=read('artifacts/eval/rag-pure-query20-2026-09-08/captures.jsonl',True)
    for i,(c,r) in enumerate(zip(ds.cases,caps,strict=True)):
        assert c.case_id==r['id'] and c.query==r['raw_query'];assert len(r['calls'])==1
        req=r['calls'][0]['request'];assert req['system']==manifest['system']
        assert req['messages']==[{'role':role,'content':text} for role,text in zip(roles[c.case_id],c.history,strict=True)]+[{'role':'user','content':'CURRENT USER MESSAGE:\n'+c.query}]
        if i<20:assert {k:v for k,v in r.items() if k!='reused'}==reused[i]
    old=read(ROOT/'Doc2Dial/cases.json.gz');src,txt,metric,_=data('Doc2Dial',old,read(ROOT/'selection.json'));scores=checked_scores(read(OUT/'scores.json.gz'),txt)
    _,chunks,_=projection(ds.documents,ds.cases,512,64,'structure_aware');byid={c.chunk_id:c for c in chunks};used=set()
    for c,r,cap in zip(ds.cases,results,caps,strict=True):
        assert r['id']==cap['id'] and r['queries']==cap['queries']
        if r['status']!='SINGLE_QUERY':assert r['agent']['recall']==0;continue
        union=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40));pool=union[:20];used.update((r['id'],cid) for cid in pool)
        assert verify(r,pool,scores,src,metric,lambda c:c,r['queries'][0])==r['agent']
        ce=r['ce_order'];assert r['stages']=={k:float(complete(c,v,byid)) for k,v in {'dense20':r['routes']['dense'],'bm25_20':r['routes']['bm25'],'union40':union,'fusion20':pool,'ce5':ce[:5],'wire5':r['agent']['ids']}.items()}
    assert used==set(scores)
    usage={k:sum(r['calls'][0].get('usage',{}).get(k,0) for r in caps[20:]) for k in ('input_tokens','output_tokens','total_tokens')}
    report={'n':100,'all_contexts_query_scores_stages_wire_verified':True,'reused_calls':20,'new_calls':sum(len(r['calls']) for r in caps[20:]),'new_call_usage':usage,'pairs':len(scores),'new_audit_model_calls':0}
    (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
