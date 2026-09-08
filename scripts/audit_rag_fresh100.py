"""Score/selection identity and provenance replay, with group bootstrap intervals."""
import json,gzip,hashlib,zipfile
from pathlib import Path
from collections import defaultdict
import numpy as np
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_rag_fresh100 import source,ROOT
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import projection,complete
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from application.knowledge_retrieval_text import build_child_retrieval_text
from scripts.run_mtrag_reranker_pair import at5
from scripts.run_wixqa_fixed_comparison import measure
from mcp.rank_fusion import fuse_rankings

def data(name,rows,selection):
    if name=='Doc2Dial':
        ds=RagDataset.load(ROOT/'doc2dial',verify_checksum=True);_,chunks,texts=projection(ds.documents,ds.cases,512,64,'structure_aware');byid={c.chunk_id:c for c in chunks};docs={d.document_id:d for d in ds.documents};cases={c.case_id:c for c in ds.cases}
        src={cid:source(cid,c.document_id,c.content,c.start_char,docs[c.document_id].title,hashlib.sha256(docs[c.document_id].content.encode()).hexdigest()) for cid,c in byid.items()};txt=dict(zip(byid,texts));hitmap={cid:dict(document_id=c.document_id,source_start_char=c.start_char,source_end_char=c.end_char) for cid,c in byid.items()}
        def metric(row,ids):
            c=cases[row['id']];m=evaluate_ranked_hits(c,ids,hitmap,top_k=5);return {'recall':float(complete(c,ids,byid)),'mrr':m['mrr'],'ndcg':m['ndcg']}
        expected={c.case_id:(c.query,c.group_id) for c in ds.cases}
    elif name=='WixQA':
        chunks=read('/tmp/dialogpilot-wixqa-full-index-20260908/chunks.json.gz');byid={c['id']:c for c in chunks};src={cid:source(cid,c['source_id'],c['text'],c['start_char'],c['title'],c['source_checksum']) for cid,c in byid.items()};txt={cid:build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for cid,c in byid.items()};cases={c['id']:c for c in selection['WixQA']}
        def metric(row,ids):
            m=measure(ids,byid,set(cases[row['id']]['article_ids']),5);return {'recall':m['article_recall'],'mrr':m['article_mrr'],'ndcg':m['article_ndcg']}
        expected={c['id']:(c['query'],c['group_id']) for c in cases.values()}
    else:
        wanted={cid for row in rows for cid in row['pool']};src={};txt={};cases={c['id']:c for c in selection['MTRAG']}
        for domain in ('clapnq','cloud','fiqa','govt'):
            p=Path('/tmp/dialogpilot-mtrag-corpora-20260907')/(domain+'.jsonl.zip')
            with zipfile.ZipFile(p) as z,z.open(domain+'.jsonl') as f:
                for line in f:
                    d=json.loads(line);cid='mtrag:'+domain+':'+d['_id']
                    if cid in wanted:src[cid]=source(cid,cid,d['text'],0,d.get('title') or '');txt[cid]=d['text']
        assert set(src)==wanted
        def metric(row,ids):
            m=at5(ids,{e['document_id'] for e in cases[row['id']]['evidence']});return {'recall':m['recall@5'],'mrr':m['mrr@5'],'ndcg':m['ndcg@5']}
        expected={c['id']:(c['query'],c['group_id']) for c in cases.values()}
    return src,txt,metric,expected

def main():
    selection=read(ROOT/'selection.json');result={}
    for name in ('Doc2Dial','MTRAG','WixQA'):
        out=ROOT/name;rows=read(out/'cases.json.gz');ss=read(out/'scores.json.gz');sc={(s['case_id'],s['cid']):s for s in ss};assert len(sc)==len(ss)
        src,txt,metric,expected=data(name,rows,selection);assert len(rows)==len(expected)==100 and {r['id'] for r in rows}==set(expected);used=set()
        for row in rows:
            assert (row['query'],row['group'])==expected[row['id']]
            pool=list(fuse_rankings(row['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20 if name=='MTRAG' else 40));assert pool==row['pool'];lookup={}
            for cid in pool:
                s=sc[row['id'],cid];used.add((row['id'],cid));assert s['query']==row['query'] and s['text_sha256']==hashlib.sha256(txt[cid].encode()).hexdigest() and np.isfinite(s['score']);lookup[cid]=s['score']
            tie=(lambda cid:pool.index(cid)) if name=='WixQA' else (lambda cid:cid)
            base=sorted(pool[:20],key=lambda cid:(-lookup[cid],tie(cid)));ce=sorted(pool,key=lambda cid:(-lookup[cid],tie(cid)));cand=ce if name=='WixQA' else list(fuse_rankings({'ce':ce,'recall':pool},weights={'ce':.75,'recall':.25},rrf_k=10,top_k=len(pool)))
            for arm,order in [('baseline',base),('candidate',cand)]:
                ids,tokens=pack(row['query'],order,src);assert {**metric(row,ids),'ids':ids,'tokens':tokens}==row['arms'][arm]
        assert used==set(sc)
        groups=defaultdict(list)
        for r in rows:groups[r['group']].append(r['arms']['candidate']['recall']-r['arms']['baseline']['recall'])
        sums=np.array([sum(v) for v in groups.values()]);counts=np.array([len(v) for v in groups.values()]);rng=np.random.default_rng(20260908);sample=rng.integers(0,len(groups),size=(20000,len(groups)));delta=sums[sample].sum(axis=1)/counts[sample].sum(axis=1)
        result[name]={'n':100,'groups':len(groups),'paired_recall_delta':sums.sum()/100,'cluster_bootstrap_95_interval':np.quantile(delta,[.025,.975]).tolist(),'all_scores_and_wire_ids_reproduced':True,'pairs':len(sc)}
    (ROOT/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
if __name__=='__main__':main()
