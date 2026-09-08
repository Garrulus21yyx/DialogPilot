"""Audit both query input lineage and cached evidence replay, zero inference."""
import json
from scripts.run_rag_other_query200 import OUT
from scripts.run_rag_fresh100 import ROOT
from scripts.replay_rag_rank_selection import read,digest
from scripts.audit_rag_fresh100 import data
from scripts.audit_rag_three_followups import checked_scores,verify
from mcp.rank_fusion import fuse_rankings

def main():
    manifest=read(OUT/'manifest.json');inputs=read(OUT/'inputs.json');caps=read(OUT/'captures.jsonl',True);sel=read(ROOT/'selection.json')
    assert len(inputs)==len(caps)==200 and digest(OUT/'inputs.json')==manifest['input_sha256'] and digest(ROOT/'selection.json')==manifest['selection_sha256']
    refpath='/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl';assert digest(refpath)==manifest['reference_sha256'];refs={r['task_id']:r for r in read(refpath,True)}
    for i,r in zip(inputs,caps,strict=True):
        assert i['id']==r['id'] and i['dataset']==r['dataset']
        if i['dataset']=='MTRAG': expected=[{'role':{'user':'user','agent':'assistant'}[m['speaker']],'content':m['text']} for m in refs[i['id']]['input']]
        else:expected=[{'role':'user','content':next(c['query'] for c in sel['WixQA'] if c['id']==i['id'])}]
        assert i['messages']==expected and len(r['calls'])==1
        req=r['calls'][0]['request'];assert req['system']==manifest['system'] and req['messages']==expected[:-1]+[{'role':'user','content':'CURRENT USER MESSAGE:\n'+expected[-1]['content']}]
    report={}
    for name in ('MTRAG','WixQA'):
        rows=read(OUT/name/'cases.json.gz');valid=[r for r in rows if 'query' in r];src,txt,metric,_=data(name,valid,sel);scores=checked_scores(read(OUT/name/'scores.json.gz'),txt);used=set()
        old={r['id']:r for r in read(ROOT/name/'cases.json.gz')};cc={r['id']:r for r in caps if r['dataset']==name}
        assert len(rows)==100 and {r['id'] for r in rows}==set(old)
        for r in rows:
            assert r['baseline']==old[r['id']]['arms']['baseline']
            if 'query' not in r:assert r['model']['recall']==0;continue
            assert cc[r['id']]['queries']==[r['query']]
            pool=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));assert pool==r['pool']
            used.update((r['id'],c) for c in pool)
            tie=(lambda c:pool.index(c)) if name=='WixQA' else (lambda c:c)
            assert verify(r,pool,scores,src,metric,tie)==r['model']
        assert used==set(scores)
        usage={k:sum(r['calls'][0].get('usage',{}).get(k,0) for r in cc.values()) for k in ('input_tokens','output_tokens','total_tokens')}
        report[name]={'n':100,'query_source_and_provider_inputs_verified':True,'scores_and_wire_verified':True,'pairs':len(scores),'usage':usage}
    (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
