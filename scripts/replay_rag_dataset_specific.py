"""Frozen dataset-specific candidate diagnostics; cached rankings/scores only."""
import json,gzip
from pathlib import Path
from scripts.replay_rag_rank_selection import read,pack,digest
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from mcp.rank_fusion import fuse_rankings
OUT=Path('artifacts/eval/rag-dataset-specific-2026-09-08')
def main():
    OUT.mkdir(exist_ok=True);assert not (OUT/'report.json').exists();selection=read(ROOT/'selection.json');report={}
    for name in ('MTRAG','WixQA'):
        rows=read(ROOT/name/'cases.json.gz');records=[]
        if name=='WixQA':
            src,txt,metric,expected=data(name,rows,selection);ss=read(ROOT/name/'scores.json.gz');scores={(s['case_id'],s['cid']):s['score'] for s in ss}
        for row in rows:
            routes=row['routes'];union=list(fuse_rankings(routes,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40));base=union[:20]
            if name=='MTRAG':
                c=next(c for c in selection[name] if c['id']==row['id']);gold={e['document_id'] for e in c['evidence']}
                reserved=list(dict.fromkeys(routes['dense'][:10]+routes['bm25'][:10]+union))[:20]
                arms={'dense20':routes['dense'],'bm25_20':routes['bm25'],'fusion20':base,'reserved10_each':reserved,'union40':union}
                records.append({'id':row['id'],'query':row['query'],'pools':arms,'candidate_recall':{a:len(set(v)&gold)/len(gold) for a,v in arms.items()},'gold':sorted(gold)})
            else:
                ar={k:list(dict.fromkeys(src[cid].document_id for cid in v)) for k,v in routes.items()}
                articles=list(fuse_rankings(ar,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20))
                representatives=[next(cid for cid in union if src[cid].document_id==aid) for aid in articles]
                pool=list(dict.fromkeys(representatives+union))[:20]
                output={}
                for a,pp in [('baseline',base),('article_rrf20',pool)]:
                    ce=sorted(pp,key=lambda cid:(-scores[row['id'],cid],union.index(cid)));ids,tokens=pack(row['query'],ce,src)
                    output[a]={**metric(row,ids),'ids':ids,'tokens':tokens,'pool':pp}
                assert {k:v for k,v in output['baseline'].items() if k!='pool'}==row['arms']['baseline']
                records.append({'id':row['id'],'arms':output})
        if name=='MTRAG':
            report[name]={'n':100,'candidate_recall':{a:sum(r['candidate_recall'][a] for r in records)/100 for a in arms},'scope':'candidate only; no final answer or rerank gain asserted'}
        else:
            report[name]={'n':100,'summary':{a:{k:sum(r['arms'][a][k] for r in records)/100 for k in ('recall','mrr','ndcg')} for a in ('baseline','article_rrf20')},'better':sum(r['arms']['article_rrf20']['recall']>r['arms']['baseline']['recall'] for r in records),'worse':sum(r['arms']['article_rrf20']['recall']<r['arms']['baseline']['recall'] for r in records)}
        (OUT/(name+'.json.gz')).write_bytes(gzip.compress(json.dumps(records).encode(),mtime=0))
    (OUT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
