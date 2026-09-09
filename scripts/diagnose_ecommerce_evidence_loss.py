"""Replay frozen evidence memberships/ranks; no retrieval or model inference."""
import gzip,json,hashlib
from pathlib import Path
from collections import Counter

def run():
    root=Path('artifacts/eval/ecommerce-failure-diagnosis-2026-09-09')
    src=Path('artifacts/eval/ecommerce-complex-v2-scope-pair120-v4/runtime/pure-cases.jsonl.gz')
    gold={x['id']:x for x in json.loads(Path('data/eval/ecommerce-complex-v2/heldout.gold.json').read_text())}
    rows=[x for x in map(json.loads,gzip.decompress(src.read_bytes()).splitlines()) if x['arm']=='scope_filtered' and x['id'] in gold]
    assert len(rows)==80 and len({r['id'] for r in rows})==80
    output=[];counts=Counter();missing_ranks=Counter()
    for r in rows:
        g=gold[r['id']];item={'id':r['id'],'topic':g['topic'],'query':r['query'],'status':r['result']['status']}
        if r['result']['status']!='OK':
            counts['upstream_failure']+=1;item['detail_code']=r['result'].get('detail_code');output.append(item);continue
        cs=r['retrieval'][0]['fused_candidates'];order=r['rerank'][0]['ordered_ids'];byid={x['chunk_id']:x for x in cs}
        assert not r['rerank'][0]['fallback'] and set(order)==set(byid)
        counts['ok_no_reranker_fallback']+=1
        units=[]
        for u in g['evidence_units']:
            matches=[x['chunk_id'] for x in cs if x['source_id']==u['source_id'] and u['quote'] in x['content']]
            assert matches,'all successful candidate sets must contain every gold unit'
            fr=min(i+1 for i,c in enumerate(cs) if c['chunk_id'] in matches)
            rr=min(order.index(cid)+1 for cid in matches)
            vis=any(e['source']['source_id']==u['source_id'] and u['quote'] in e['text'] for e in r['wire']['evidence'])
            assert vis==(rr<=5),'test claim: no extra pack/wire loss'
            units.append({'unit_id':u['unit_id'],'quote':u['quote'],'fusion_rank':fr,'rerank_rank':rr,'visible':vis})
            if not vis:missing_ranks[rr]+=1
        top=[]
        for rank,cid in enumerate(order[:5],1):
            c=byid[cid];cov=[u['unit_id'] for u in g['evidence_units'] if c['source_id']==u['source_id'] and u['quote'] in c['content']]
            top.append({'rank':rank,'source_id':c['source_id'],'start':c['source_start_char'],'end':c['source_end_char'],
                        'section_count':c['content'].count('## '),'has_target_topic':g['topic'] in c['content'],'covered_units':cov})
        full=all(u['visible'] for u in units)
        counts['complete_wire' if full else 'rerank_loss']+=1
        counts['complete_fusion_top5']+=all(u['fusion_rank']<=5 for u in units)
        if not full:
            counts['missing_units']+=sum(not u['visible'] for u in units)
            counts['lost_after_fusion_top5']+=sum(not u['visible'] and u['fusion_rank']<=5 for u in units)
            counts['no_gold_slots_in_failed_top5']+=sum(not x['covered_units'] for x in top)
            counts['duplicate_gold_slots_in_failed_top5']+=sum(len(x['covered_units']) for x in top)-len({u for x in top for u in x['covered_units']})
        item.update(units=units,top5=top,full=full,rerank_query=r['rerank'][0]['query'],variants=r['retrieval'][0]['variants'],rewrite_fallback=r['result']['trace']['rewrite_fallback'],unchanged_with_history=bool(r['history']) and r['rerank'][0]['query']==r['original']);output.append(item)
    root.joinpath('evidence-ranks.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    summary={'counts':dict(counts),'missing_best_ce_ranks':dict(sorted(missing_ranks.items())),
             'input_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),'API_calls':0,'new_CE':0,
             'scope':'frozen already-consumed80; ranking membership diagnosis, not strategy gain'}
    root.joinpath('summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=2))
    for x in output:
        if x.get('full') is False:print(x['id'],[(u['unit_id'],u['fusion_rank'],u['rerank_rank']) for u in x['units'] if not u['visible']], 'empty_slots',sum(not t['covered_units'] for t in x['top5']))
if __name__=='__main__':run()
