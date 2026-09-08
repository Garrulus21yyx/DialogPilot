"""Zero-inference audit of frozen query contrast and paired uncertainty."""
import hashlib,json,math
import numpy as np
from scripts.run_rag_resolved_query100 import OUT
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from scripts.replay_rag_rank_selection import read,digest,pack
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import projection,complete
from mcp.rank_fusion import fuse_rankings

def main():
    manifest=read(OUT/'manifest.json');assert digest(OUT/'queries.json')==manifest['query_sha256']
    rows=read(OUT/'cases.json.gz');queries=read(OUT/'queries.json');old=read(ROOT/'Doc2Dial/cases.json.gz')
    ss=read(OUT/'scores.json.gz');scores={(s['id'],s['cid']):s for s in ss};assert len(scores)==len(ss)==2000
    src,txt,metric,expected=data('Doc2Dial',old,read(ROOT/'selection.json'))
    ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True);_,chunks,_=projection(ds.documents,ds.cases,512,64,'structure_aware');byid={c.chunk_id:c for c in chunks}
    used=set()
    for r,q,c,o in zip(rows,queries,ds.cases,old,strict=True):
        assert r['id']==q['id']==c.case_id==o['id'] and r['query']==q['resolved_query']
        assert q['query']==c.query and q['history']==list(c.history)
        union=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40));pool=union[:20]
        lookup={}
        for cid in pool:
            s=scores[r['id'],cid];used.add((r['id'],cid));assert s['query']==r['query'] and s['text_sha256']==hashlib.sha256(txt[cid].encode()).hexdigest() and math.isfinite(s['score']);lookup[cid]=s['score']
        ce=sorted(pool,key=lambda cid:(-lookup[cid],cid));assert ce==r['ce_order']
        ids,tokens=pack(r['query'],ce,src);assert r['resolved']=={**metric(o,ids),'ids':ids,'tokens':tokens} and r['baseline']==o['arms']['baseline']
        stages={'dense20':r['routes']['dense'],'bm25_20':r['routes']['bm25'],'union40':union,'fusion20':pool,'ce5':ce[:5],'wire5':ids}
        assert r['stages']=={k:float(complete(c,v,byid)) for k,v in stages.items()}
    assert used==set(scores)
    delta=np.array([r['resolved']['recall']-r['baseline']['recall'] for r in rows]);rng=np.random.default_rng(20260908)
    boot=delta[rng.integers(0,len(rows),size=(20000,len(rows)))].mean(axis=1)
    result={'n':100,'pairs':2000,'all_inputs_scores_pack_wire_verified':True,'paired_recall_delta':float(delta.mean()),'paired_bootstrap_95':np.quantile(boot,[.025,.975]).tolist(),'api_calls':0,'new_inferences':0}
    (OUT/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
