"""Frozen three-dataset candidate selection ablation: no API or new scores."""
import gzip,json,hashlib,zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from mcp.rank_fusion import fuse_rankings
from mcp.context_packer import ContextCandidate,ContextPacker
from mcp.evidence_pack import EvidencePack
from mcp.tool_manager import MCPToolManager,ToolResult
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection,ranked,complete
from scripts.run_mtrag_reranker_pair import at5
from scripts.run_wixqa_fixed_comparison import measure

ROOT=Path('artifacts/eval/rag-rank-selection-three-2026-09-08')
def read(p,lines=False):
    b=Path(p).read_bytes();s=gzip.decompress(b).decode() if str(p).endswith('.gz') else b.decode()
    return [json.loads(l) for l in s.splitlines()] if lines else json.loads(s)
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def orders(candidate,ce,source,diversity=False):
    assert set(candidate)==set(ce) and len(set(ce))==len(ce)
    result={'baseline':ce}
    for w in (.75,.5):result['ce_'+str(w)]=list(fuse_rankings({'ce':ce,'recall':candidate},weights={'ce':w,'recall':1-w},rrf_k=10,top_k=20))
    if diversity:
        seen=set();first=[];rest=[]
        for cid in ce:
            sid=source[cid].document_id
            (rest if sid in seen else first).append(cid);seen.add(sid)
        result['article_first']=first+rest
    return result

def pack(query,order,source):
    packed=ContextPacker().pack([source[cid] for cid in order],max_tokens=2600,max_chunks=5)
    ep=EvidencePack.from_packed(query,packed,retrieval_policy={'vector_weight':.5,'lexical_weight':.5})
    data={'status':'OK','evidence_pack':ep.to_dict(include_text=True)} if packed.selected else {'status':'NO_EVIDENCE','evidence_pack':None}
    wire=json.loads(MCPToolManager._render_for_model(None,ToolResult(True,data,'knowledge_search',authority='knowledge.active_source')))
    evidence=wire.get('evidence',[])
    for e,c in zip(evidence,packed.selected,strict=True):
        assert e['text']==c.text and e['source']['source_id']==c.document_id
    assert packed.token_count<=2600 and len(evidence)<=5
    return list(packed.chunk_ids),packed.token_count

def doc():
    root=Path('artifacts/eval/rag-doc2dial-balanced300-2026-09-08')
    snapshot=read('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz')['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory() as temp:
        for name,text in snapshot.items():Path(temp,name).write_text(text)
        ds=RagDataset.load(Path(temp),verify_checksum=True)
    cases=ds.select_cases('dev');_,chunks,_=projection(ds.documents,cases,512,64,'structure_aware')
    docs={d.document_id:d for d in ds.documents};byid={c.chunk_id:c for c in chunks};ids=list(byid)
    src={cid:ContextCandidate(cid,c.document_id,c.content,c.start_char,c.end_char,title=docs[c.document_id].title,source_checksum=hashlib.sha256(docs[c.document_id].content.encode()).hexdigest(),source_revision='frozen-doc',index_manifest_fingerprint='frozen-doc') for cid,c in byid.items()}
    scorefile='artifacts/eval/rag-local-parent-pair-v2-2026-09-07/scores.npz'
    assert digest(scorefile)==read('artifacts/eval/rag-g3-membership-dev300-2026-09-07/report.json')['scores_sha256']
    scores=np.load(scorefile);old=read(root/'cases.jsonl.gz',True);output=[]
    hitmap={cid:dict(document_id=c.document_id,source_start_char=c.start_char,source_end_char=c.end_char) for cid,c in byid.items()}
    for i,(c,r) in enumerate(zip(cases,old,strict=True)):
        assert c.case_id==r['case_id'] and c.query==r['query']
        lex=scores['lexical'][i];routes={'dense':ranked(scores['dense'][i],ids,20),'bm25':[ids[j] for j in sorted(range(len(ids)),key=lambda j:(-lex[j],ids[j])) if lex[j]>0][:20]}
        candidates=list(fuse_rankings(routes,weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));ce=r['arms']['balanced']['reranked_ids'];arms={}
        for name,order in orders(candidates,ce,src).items():
            selected,tokens=pack(c.query,order,src)
            m=evaluate_ranked_hits(c,selected,hitmap,top_k=5);arms[name]={'recall':float(complete(c,selected,byid)),'mrr':m['mrr'],'ndcg':m['ndcg'],'ids':selected,'tokens':tokens}
            if name=='baseline':assert selected==r['arms']['balanced']['packed_ids']
        output.append({'id':c.case_id,'candidate_recall':float(complete(c,candidates,byid)),'arms':arms})
    return output

def mtrag():
    root=Path('artifacts/eval/rag-mtrag-balanced35-2026-09-08');hybrid=read(root/'hybrid/cases.json.gz');rr=read(root/'rerank/cases.json.gz');old=read(root/'pack/cases.json.gz')
    wanted={cid for r in rr for cid in r['arms']['0.5']['ranking']};src={}
    manifest=read('/tmp/dialogpilot-mtrag-adapted-v2-20260907/manifest.json')
    for domain in manifest['source']['archives']:
        p=Path('/tmp/dialogpilot-mtrag-corpora-20260907')/(domain+'.jsonl.zip');assert digest(p)==manifest['source']['archives'][domain]['sha256']
        with zipfile.ZipFile(p) as z,z.open(domain+'.jsonl') as f:
            for line in f:
                d=json.loads(line);cid='mtrag:'+domain+':'+d['_id']
                if cid in wanted:src[cid]=ContextCandidate(cid,cid,d['text'],0,len(d['text']),title=d.get('title') or '',source_checksum=hashlib.sha256(d['text'].encode()).hexdigest(),source_revision=manifest['source']['revision'],index_manifest_fingerprint=digest('/tmp/dialogpilot-mtrag-adapted-v2-20260907/manifest.json'))
    assert set(src)==wanted
    output=[]
    for h,r,o in zip(hybrid,rr,old,strict=True):
        assert h['case_id']==r['case_id']==o['case_id'] and h['gold']==r['gold']==o['gold'];arms={}
        candidate=h['variants']['0.5']['ranking']
        for name,order in orders(candidate,r['arms']['0.5']['ranking'],src).items():
            selected,tokens=pack(h['query'],order,src);m=at5(selected,set(r['gold']))
            arms[name]={'recall':m['recall@5'],'mrr':m['mrr@5'],'ndcg':m['ndcg@5'],'ids':selected,'tokens':tokens}
            if name=='baseline':assert selected==o['arms']['0.5']['packed_ids']
        output.append({'id':r['case_id'],'candidate_recall':len(set(candidate)&set(r['gold']))/len(set(r['gold'])),'arms':arms})
    return output

def wix():
    cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908');identity=read(cache/'COMPLETE.json');assert digest(cache/'chunks.json.gz')==identity['chunks_sha256']
    chunks=read(cache/'chunks.json.gz');byid={c['id']:c for c in chunks}
    src={cid:ContextCandidate(cid,c['source_id'],c['text'],c['start_char'],c['end_char'],title=c['title'],source_checksum=c['source_checksum'],source_revision='frozen-wix',index_manifest_fingerprint=identity['chunks_sha256']) for cid,c in byid.items()}
    output=[]
    for r in read('artifacts/eval/wixqa-fixed-heldout20-2026-09-08/cases.jsonl.gz',True):
        a=r['arms']['0.5'];arms={}
        for name,order in orders(a['candidate_ids'],a['ce_order'],src,True).items():
            selected,tokens=pack(r['case']['query'],order,src);m=measure(selected,byid,set(r['case']['article_ids']),5)
            arms[name]={'recall':m['article_recall'],'mrr':m['article_mrr'],'ndcg':m['article_ndcg'],'ids':selected,'tokens':tokens}
            if name=='baseline':assert selected==a['packed_ids']
        output.append({'id':r['case']['group_id'],'candidate_recall':a['candidate20']['article_recall'],'arms':arms})
    return output

def main():
    ROOT.mkdir(exist_ok=False)
    (ROOT/'manifest.json').write_text(json.dumps({'gap':'R04/R05','api_calls':0,'new_scores':0,'script_sha256':digest(__file__),'arms':['baseline','ce_0.75','ce_0.5','Wix-only article_first'],'budget':{'candidate':20,'final':5,'tokens':2600},'scope':'Consumed development selection; frozen queries/candidates; no production adoption'},indent=2)+'\n')
    report={}
    for name,fn,expected in [('Doc2Dial',doc,208/300),('MTRAG',mtrag,.4414285714285714),('WixQA',wix,.675)]:
        rows=fn();n=len(rows);summary={arm:{k:sum(r['arms'][arm][k] for r in rows)/n for k in ('recall','mrr','ndcg')} for arm in rows[0]['arms']}
        assert abs(summary['baseline']['recall']-expected)<1e-10
        paired={arm:{'better':sum(r['arms'][arm]['recall']>r['arms']['baseline']['recall'] for r in rows),'worse':sum(r['arms'][arm]['recall']<r['arms']['baseline']['recall'] for r in rows)} for arm in summary if arm!='baseline'}
        report[name]={'n':n,'candidate_recall':sum(r['candidate_recall'] for r in rows)/n,'summary':summary,'paired':paired}
        (ROOT/(name+'.json.gz')).write_bytes(gzip.compress(json.dumps(rows).encode(),mtime=0));(ROOT/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(name,json.dumps(report[name]),flush=True)
if __name__=='__main__':main()
