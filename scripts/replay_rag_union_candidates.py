"""Bounded union of two existing top-20 routes, scoring only uncached pairs."""
import json,gzip,hashlib,time
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from scripts.replay_rag_rank_selection import read,digest,pack
from mcp.context_packer import ContextCandidate
from mcp.rank_fusion import fuse_rankings
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection,ranked,complete
from application.knowledge_retrieval_text import build_child_retrieval_text
from infrastructure.local_knowledge_reranker import LocalKnowledgeReranker
from scripts.run_wixqa_fixed_comparison import measure
ROOT=Path('artifacts/eval/rag-union-selection-2026-09-08')

def doc_inputs():
    snapshot=read('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz')['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory() as temp:
        for name,text in snapshot.items():Path(temp,name).write_text(text)
        ds=RagDataset.load(Path(temp),verify_checksum=True)
    cases=ds.select_cases('dev');_,chunks,texts=projection(ds.documents,cases,512,64,'structure_aware');ids=[c.chunk_id for c in chunks];byid=dict(zip(ids,chunks));docs={d.document_id:d for d in ds.documents}
    source={cid:ContextCandidate(cid,c.document_id,c.content,c.start_char,c.end_char,title=docs[c.document_id].title,source_checksum=hashlib.sha256(docs[c.document_id].content.encode()).hexdigest(),source_revision='frozen-doc',index_manifest_fingerprint='frozen-doc') for cid,c in byid.items()}
    hitmap={cid:dict(document_id=c.document_id,source_start_char=c.start_char,source_end_char=c.end_char) for cid,c in byid.items()}
    old=Path('artifacts/eval/rag-local-selection-stages-2026-09-07');manifest=read(old/'manifest.json');assert manifest['dataset']==ds.manifest
    scorepath=Path('artifacts/eval/rag-local-parent-pair-v2-2026-09-07/scores.npz');assert digest(scorepath)==read('artifacts/eval/rag-g3-membership-dev300-2026-09-07/report.json')['scores_sha256'];scores=np.load(scorepath)
    values=np.load(old/'crossencoder-scores.npz')['values'];locations=[(i,cid) for i,order in enumerate(manifest['candidate_orders']) for cid in order];assert len(values)==len(locations);ce=dict(zip(locations,map(float,values)))
    extra=Path('artifacts/eval/rag-g3-membership-stages-2026-09-07');audit=read(extra/'report.json')['audit']
    assert audit['model_sha256']==manifest['reranker_model_sha256']
    assert audit['current_input_sha256']==hashlib.sha256(json.dumps({'queries':[c.query for c in cases],'texts':texts},ensure_ascii=False).encode()).hexdigest()
    for root in [extra,Path('artifacts/eval/rag-doc2dial-balanced300-2026-09-08')]:
        for v in read(root/'added-scores.json.gz'):ce[v['case_index'],v['candidate_id']]=v['score']
    baseline=read('artifacts/eval/rag-doc2dial-balanced300-2026-09-08/cases.jsonl.gz',True);out=[]
    for i,(case,oldrow) in enumerate(zip(cases,baseline,strict=True)):
        lex=scores['lexical'][i];routes={'dense':ranked(scores['dense'][i],ids,20),'bm25':[ids[j] for j in sorted(range(len(ids)),key=lambda j:(-lex[j],ids[j])) if lex[j]>0][:20]}
        candidate=list(fuse_rankings(routes,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40))
        assert oldrow['case_id']==case.case_id
        out.append({'id':case.case_id,'query':case.query,'candidate':candidate,'baseline_ids':oldrow['arms']['balanced']['packed_ids'],'scores':{cid:ce[i,cid] for cid in candidate if (i,cid) in ce},'case':case})
    def metric(row,selected):
        m=evaluate_ranked_hits(row['case'],selected,hitmap,top_k=5)
        return {'recall':float(complete(row['case'],selected,byid)),'mrr':m['mrr'],'ndcg':m['ndcg']}
    return out,source,dict(zip(ids,texts)),metric,manifest['reranker_model_sha256']

def wix_inputs():
    cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908');assert digest(cache/'chunks.json.gz')==read(cache/'COMPLETE.json')['chunks_sha256']
    chunks=read(cache/'chunks.json.gz');byid={c['id']:c for c in chunks};source={cid:ContextCandidate(cid,c['source_id'],c['text'],c['start_char'],c['end_char'],title=c['title'],source_checksum=c['source_checksum'],source_revision='frozen-wix',index_manifest_fingerprint='frozen-wix') for cid,c in byid.items()}
    texts={cid:build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for cid,c in byid.items()};out=[]
    for r in read('artifacts/eval/wixqa-fixed-heldout20-2026-09-08/cases.jsonl.gz',True):
        out.append({'id':r['case']['group_id'],'query':r['case']['query'],'candidate':list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=40)),'baseline_ids':r['arms']['0.5']['packed_ids'],'scores':r['scores'],'gold':set(r['case']['article_ids'])})
    def metric(row,selected):
        m=measure(selected,byid,row['gold'],5);return {'recall':m['article_recall'],'mrr':m['article_mrr'],'ndcg':m['article_ndcg']}
    return out,source,texts,metric,read('artifacts/eval/wixqa-fixed-heldout20-2026-09-08/identity.json')['reranker']['artifacts']['model.safetensors']

def main():
    ROOT.mkdir(exist_ok=False);manifest={'candidate_budget':40,'per_route':20,'final_k':5,'tokens':2600,'api_calls':0,'new_document_embeddings':0,'max_new_ce_pairs':6500,'arms':['baseline20','union_ce','union_ce_0.75','Wix-only union_article_first'],'script_sha256':digest(__file__),'adopted':False,'scope':'Consumed dev, new CE cost disclosed; no Agent changes'}
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    scorer=LocalKnowledgeReranker('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',device='cuda',batch_size=4);report={};total=0
    for name,loader,expected in [('Doc2Dial',doc_inputs,208/300),('WixQA',wix_inputs,.675)]:
        rows,src,texts,metric,model_sha=loader();assert scorer.identity['artifacts']['model.safetensors']==model_sha
        result=[];added=[];start=time.monotonic()
        for i,row in enumerate(rows):
            missing=[cid for cid in row['candidate'] if cid not in row['scores']];assert total+len(missing)<=6500
            vals=scorer._score(row['query'],[texts[cid] for cid in missing]) if missing else [];total+=len(missing)
            for cid,v in zip(missing,vals,strict=True):row['scores'][cid]=v;added.append({'case_id':row['id'],'query':row['query'],'candidate_id':cid,'input_sha256':hashlib.sha256(texts[cid].encode()).hexdigest(),'score':v})
            tie=(lambda cid:cid) if name=='Doc2Dial' else (lambda cid:row['candidate'].index(cid))
            ce=sorted(row['candidate'],key=lambda cid:(-row['scores'][cid],tie(cid)))
            orders={'baseline20':row['baseline_ids'],'union_ce':ce,'union_ce_0.75':list(fuse_rankings({'ce':ce,'recall':row['candidate']},weights={'ce':.75,'recall':.25},rrf_k=10,top_k=40))}
            if name=='WixQA':
                seen=set();first=[];rest=[]
                for cid in ce:
                    sid=src[cid].document_id;(rest if sid in seen else first).append(cid);seen.add(sid)
                orders['union_article_first']=first+rest
            arms={}
            for arm,order in orders.items():
                selected,tokens=pack(row['query'],order,src);arms[arm]={**metric(row,selected),'ids':selected,'tokens':tokens}
                if arm=='baseline20':assert selected==row['baseline_ids']
            result.append({'id':row['id'],'arms':arms,'candidate_ids':row['candidate'],'candidate_recall':metric(row,row['candidate'])['recall']})
            if i%30==0:print(name,i,'new_ce',total,flush=True)
        (ROOT/(name+'-new-scores.json.gz')).write_bytes(gzip.compress(json.dumps(added).encode(),mtime=0));(ROOT/(name+'.json.gz')).write_bytes(gzip.compress(json.dumps(result).encode(),mtime=0))
        n=len(result);summary={arm:{k:sum(r['arms'][arm][k] for r in result)/n for k in ('recall','mrr','ndcg')} for arm in result[0]['arms']};assert abs(summary['baseline20']['recall']-expected)<1e-10
        report[name]={'n':n,'new_ce_pairs':len(added),'wall_seconds':time.monotonic()-start,'candidate_recall':sum(r['candidate_recall'] for r in result)/n,'summary':summary,'paired':{arm:{'better':sum(r['arms'][arm]['recall']>r['arms']['baseline20']['recall'] for r in result),'worse':sum(r['arms'][arm]['recall']<r['arms']['baseline20']['recall'] for r in result)} for arm in summary if arm!='baseline20'}}
        (ROOT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(name,report[name],flush=True)
if __name__=='__main__':main()
