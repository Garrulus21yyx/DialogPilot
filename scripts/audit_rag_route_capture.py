"""Zero-API validation of captured real route pools and fused order."""
import argparse,gzip,json
from pathlib import Path
from mcp.rank_fusion import fuse_rankings

def audit(root):
    rows=json.loads(gzip.decompress((root/'source-route-captures.json.gz').read_bytes()))
    ok=[r for r in rows if r.get('status')=='OK'];extras=[]
    for r in ok:
        rankings={route:tuple(sorted(rs,key=lambda i:(rs[i],i))) for route,rs in r['route_ranks'].items()}
        replay=fuse_rankings(rankings,weights=r['route_weights'],rrf_k=r['rrf_k'],top_k=r['top_k'])
        assert list(replay)==[c['chunk_id'] for c in r['fused_candidates']]
        assert r['projection_complete'] and r['selected_projection_matches']
        extras.append(len(r['source_union'])-len(r['fused_candidates']))
    result={'api_calls':0,'captured_searches':len(rows),'successful_searches':len(ok),'fusion_exact_replays':len(ok),
            'projection_checks_passed':len(ok),'searches_with_extra_unselected_candidates':sum(n>0 for n in extras),
            'unselected_candidate_appearances':sum(extras),'capture_errors':sum('capture_error' in r for r in rows),
            'scope':'Integration replay; scoped and omitted-scope searches count separately. Not query count, Recall, or production latency.'}
    (root/'capture-audit.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path)
    print(json.dumps(audit(parser.parse_args().root)))
