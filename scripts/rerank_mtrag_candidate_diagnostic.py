"""Score missing union pairs once, compare preregistered MTRAG pools."""
import json,gzip,hashlib
from pathlib import Path
from scripts.replay_rag_rank_selection import read,pack
from scripts.replay_rag_dataset_specific import OUT
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker

def main():
    assert not (OUT/'MTRAG-final-report.json').exists()
    rr=read(OUT/'MTRAG.json.gz');old=read(ROOT/'MTRAG/cases.json.gz');expanded=[{**r,'pool':d['pools']['union40']} for r,d in zip(old,rr,strict=True)]
    src,txt,metric,expected=data('MTRAG',expanded,read(ROOT/'selection.json'))
    ss=read(ROOT/'MTRAG/scores.json.gz');lookup={}
    for s in ss:
        assert s['text_sha256']==hashlib.sha256(txt[s['cid']].encode()).hexdigest()
        lookup[s['case_id'],s['cid']]=s['score']
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4);assert scorer.identity==read(ROOT/'MTRAG/identity.json')['reranker']
    added=[];results=[]
    for row,o in zip(rr,old,strict=True):
        missing=[c for c in row['pools']['union40'] if (row['id'],c) not in lookup]
        sc=scorer._score(row['query'],[txt[c] for c in missing]) if missing else []
        for cid,s in zip(missing,sc,strict=True):
            lookup[row['id'],cid]=s;added.append({'case_id':row['id'],'cid':cid,'query':row['query'],'text_sha256':hashlib.sha256(txt[cid].encode()).hexdigest(),'score':s})
        assert len(added)<=2000
        arms={}
        for a in ('fusion20','dense20','reserved10_each','union40'):
            ce=sorted(row['pools'][a],key=lambda c:(-lookup[row['id'],c],c));ids,tokens=pack(row['query'],ce,src);arms[a]={**metric(row,ids),'ids':ids,'tokens':tokens}
        assert arms['fusion20']==o['arms']['baseline'];results.append({'id':row['id'],'arms':arms})
    report={'n':100,'api_calls':0,'added_ce_pairs':len(added),'summary':{a:{k:sum(r['arms'][a][k] for r in results)/100 for k in ('recall','mrr','ndcg')} for a in arms},'paired':{a:{'better':sum(r['arms'][a]['recall']>r['arms']['fusion20']['recall'] for r in results),'worse':sum(r['arms'][a]['recall']<r['arms']['fusion20']['recall'] for r in results)} for a in arms}}
    for n,v in [('MTRAG-added-scores',added),('MTRAG-final',results)]: (OUT/(n+'.json.gz')).write_bytes(gzip.compress(json.dumps(v).encode(),mtime=0))
    (OUT/'MTRAG-final-report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
