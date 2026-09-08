"""Zero-model reproduction of every chosen passage and all scored unions."""
import json,math
from pathlib import Path
from scripts.replay_rag_rank_selection import doc,mtrag,wix,read,digest,pack
from scripts.replay_rag_union_candidates import doc_inputs,wix_inputs
from mcp.rank_fusion import fuse_rankings

def main():
    root=Path('artifacts/eval/rag-rank-selection-three-2026-09-08')
    for name,fn in [('Doc2Dial',doc),('MTRAG',mtrag),('WixQA',wix)]:
        assert fn()==read(root/(name+'.json.gz'))
    union=Path('artifacts/eval/rag-union-selection-2026-09-08');counts={}
    for name,loader in [('Doc2Dial',doc_inputs),('WixQA',wix_inputs)]:
        rows,src,texts,metric,_=loader();saved=read(union/(name+'.json.gz'));new=read(union/(name+'-new-scores.json.gz'))
        scores={(v['case_id'],v['candidate_id']):v for v in new};assert len(scores)==len(new)
        used=set()
        for row,expected in zip(rows,saved,strict=True):
            assert row['id']==expected['id'] and row['candidate']==expected['candidate_ids']
            for cid in row['candidate']:
                if cid not in row['scores']:
                    k=(row['id'],cid);v=scores[k];used.add(k)
                    assert v['query']==row['query'] and v['input_sha256']==__import__('hashlib').sha256(texts[cid].encode()).hexdigest() and math.isfinite(v['score'])
                    row['scores'][cid]=v['score']
            tie=(lambda cid:cid) if name=='Doc2Dial' else (lambda cid:row['candidate'].index(cid))
            ce=sorted(row['candidate'],key=lambda cid:(-row['scores'][cid],tie(cid)))
            orders={'baseline20':row['baseline_ids'],'union_ce':ce,'union_ce_0.75':list(fuse_rankings({'ce':ce,'recall':row['candidate']},weights={'ce':.75,'recall':.25},rrf_k=10,top_k=40))}
            if name=='WixQA':
                seen=set();first=[];rest=[]
                for cid in ce:
                    sid=src[cid].document_id;(rest if sid in seen else first).append(cid);seen.add(sid)
                orders['union_article_first']=first+rest
            for arm,order in orders.items():
                ids,tokens=pack(row['query'],order,src)
                assert {'ids':ids,'tokens':tokens,**metric(row,ids)}==expected['arms'][arm]
        assert used==set(scores);counts[name]=len(used)
    result={'all_rank_arms_reproduced':True,'all_union_arms_reproduced':True,'new_scores_identity_validated':counts,'api_calls':0,'new_model_calls':0}
    (union/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(result)
if __name__=='__main__':main()
