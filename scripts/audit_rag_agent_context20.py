"""Audit actual context-window, model captures, and emitted-query retrieval."""
import json,hashlib,math
from collections import Counter
from scripts.run_rag_agent_context20 import OUT
from scripts.replay_rag_rank_selection import read,digest,pack
from scripts.run_rag_fresh100 import ROOT
from scripts.audit_rag_fresh100 import data
from mcp.rank_fusion import fuse_rankings

def main():
    rows=read(OUT/'captures.jsonl',True);manifest=read(OUT/'manifest.json');assert len(rows)==20 and [r['id'] for r in rows]==manifest['ids']
    assert all(digest(p)==h for p,h in manifest['sources'].items())
    for r in rows:
        loaded=r['loaded_context'];messages=loaded['recent_messages'];assert len(messages)==8
        assert [m['content'] for m in messages]==[x.strip() for x in r['original_history'][-8:]]
        assert [m['role'] for m in messages]==list(r['roles'][-8:])
        assert json.loads(loaded['summary']['content']) == {'chunks': [], 'covered_until_seq': 0}
        assert not loaded['knowledge_evidence']
        assert len(r['calls'])==1 and 'raw_output' in r['calls'][0]
        sent=r['calls'][0]['request']['messages'];assert [(m['role'],m['content']) for m in sent[1:-1]]==[(m['role'],m['content']) for m in messages]
        assert r['raw_query'] in json.dumps(sent[-1],ensure_ascii=False)
    retrieval=read(OUT/'retrieval.json');ss=read(OUT/'scores.json.gz');scores={(r['id'],r['cid']):r for r in ss};assert len(scores)==len(ss)
    old=read(ROOT/'Doc2Dial/cases.json.gz');src,txt,metric,expected=data('Doc2Dial',old,read(ROOT/'selection.json'));used=set()
    for r in retrieval:
        if r['status']!='SINGLE_QUERY':continue
        pool=list(fuse_rankings(r['routes'],weights={'dense':.5,'bm25':.5},rrf_k=10,top_k=20));lookup={}
        for cid in pool:
            s=scores[r['id'],cid];used.add((r['id'],cid));assert s['query']==r['queries'][0] and s['text_sha256']==hashlib.sha256(txt[cid].encode()).hexdigest() and math.isfinite(s['score']);lookup[cid]=s['score']
        ce=sorted(pool,key=lambda c:(-lookup[c],c));assert ce==r['ce_order'];ids,tokens=pack(r['queries'][0],ce,src)
        assert r['agent']=={**metric(r,ids),'ids':ids,'tokens':tokens}
    assert used==set(scores)
    dispositions=Counter(r['proposal']['disposition'] for r in rows);delegates=[r['id'] for r in rows if any(c['kind']=='DELEGATE_TASK' for c in r['proposal']['commands'])]
    usage={k:sum(c.get('usage',{}).get(k,0) for r in rows for c in r['calls']) for k in ('input_tokens','output_tokens','total_tokens')}
    report={'all_20_windows_and_provider_inputs_verified':True,'source_hashes_unchanged':True,'all_query_scores_pack_wire_verified':True,'dispositions':dict(dispositions),'delegations_not_executed':delegates,'usage':usage,'sum_latency_ms':sum(c['latency_ms'] for r in rows for c in r['calls'])}
    (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
