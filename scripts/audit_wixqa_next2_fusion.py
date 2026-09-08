"""Same-query fusion replay using frozen production route ranks, no API."""
import gzip,json
from pathlib import Path
ROOT=Path('artifacts/eval/wixqa-real-entry-next2-2026-09-08')
def main():
    output={}
    for arm in ('0.25','0.5'):
        p=ROOT/arm
        s=json.loads(gzip.decompress((p/'source-route-captures.json.gz').read_bytes()))[0]
        gold=set(json.loads((p/'selection.json').read_text())['cases'][0]['article_ids'])
        replay={}
        for weight in (.25,.5):
            scores={c:sum((weight if route.endswith(':vector') else 1-weight)/(s['rrf_k']+ranks[c]) for route,ranks in s['route_ranks'].items() if c in ranks) for c in s['source_union']}
            order=sorted(scores,key=lambda c:(-scores[c],c))[:20]
            if weight==float(arm):assert order==[c['chunk_id'] for c in s['fused_candidates']]
            replay[str(weight)]=[i+1 for i,c in enumerate(order) if s['source_union'][c]['source_id'] in gold]
        output[arm]={'query':s['query'],'route_gold_ranks':{route:[rank for c,rank in ranks.items() if s['source_union'][c]['source_id'] in gold] for route,ranks in s['route_ranks'].items()},'replayed_gold_positions':replay}
    (ROOT/'fusion-replay.json').write_text(json.dumps({'scope':'same frozen query per row; candidate only, no rerank or answer replay','new_api_calls':0,'arms':output},indent=2)+'\n')
    print(output)
if __name__=='__main__':main()
