"""Zero-inference replay of joint pools, score provenance and final evidence."""
import hashlib
import json
import math
from scripts.run_rag_raw_standalone300 import OUT
from scripts.run_rag_fresh100 import ROOT
from scripts.replay_rag_rank_selection import read, pack
from scripts.audit_rag_fresh100 import data
from mcp.rank_fusion import fuse_rankings


def main():
    selection = read(ROOT / 'selection.json')
    reports = {}
    for name in ('Doc2Dial', 'MTRAG', 'WixQA'):
        rows = read(OUT / name / 'cases.json.gz')
        old = {r['id']: r for r in read(ROOT / name / 'cases.json.gz')}
        basepath = ('artifacts/eval/rag-pure-query100-2026-09-08' if name == 'Doc2Dial'
                    else f'artifacts/eval/rag-query-other200-2026-09-08/{name}')
        generated = read(basepath + ('/retrieval.json' if name == 'Doc2Dial' else '/cases.json.gz'))
        generated = {r['id']: r for r in generated}
        combined = [{**r, 'pool': list({c for route in r['routes'].values() for c in route})} for r in rows]
        src, txt, metric, _ = data(name, combined, selection)
        scores = {}
        added = read(OUT / name / 'added-scores.json.gz')
        for s in read(basepath + '/scores.json.gz') + added:
            assert s['text_sha256'] == hashlib.sha256(txt[s['cid']].encode()).hexdigest()
            assert math.isfinite(s['score'])
            key = (s['query'], s['cid'])
            if key in scores:
                assert scores[key] == s['score']
            scores[key] = s['score']
        assert len(rows) == 100 and {r['id'] for r in rows} == set(old)
        inputs = {r['id']: r['messages'][-1]['content'] for r in read('artifacts/eval/rag-query-other200-2026-09-08/inputs.json')}
        used = set()
        for r in rows:
            g = generated[r['id']]
            q = g['queries'][0] if name == 'Doc2Dial' else g['query']
            assert q == r['standalone_query']
            assert r['arms']['standalone'] == (g['agent'] if name == 'Doc2Dial' else g['model'])
            assert r['raw_query'] == (inputs[r['id']] if name == 'MTRAG' else old[r['id']]['query'])
            for route in ('dense', 'bm25'):
                assert r['routes']['standalone_' + route] == g['routes'][route]
                if name != 'MTRAG':
                    assert r['routes']['raw_' + route] == old[r['id']]['routes'][route]
            if r['raw_query'] == q:
                pool = list(fuse_rankings(g['routes'], weights={'dense': .5, 'bm25': .5}, rrf_k=10, top_k=20))
            else:
                pool = list(fuse_rankings(r['routes'], weights={'raw_dense': .125, 'raw_bm25': .125, 'standalone_dense': .375, 'standalone_bm25': .375}, rrf_k=10, top_k=20))
            assert pool == r['joint_pool'] and len(set(pool)) == len(pool) <= 20
            def replay(query, candidates):
                used.update((query, c) for c in candidates)
                order = sorted(candidates, key=lambda c: (-scores[query, c], candidates.index(c) if name == 'WixQA' else c))
                ids, tokens = pack(query, order, src)
                return order, {**metric(r, ids), 'ids': ids, 'tokens': tokens}
            order, result = replay(q, pool)
            assert order == r['joint_ce'] and result == r['arms']['joint']
            if name == 'MTRAG':
                raw_routes = {k: r['routes']['raw_' + k] for k in ('dense', 'bm25')}
                raw_pool = list(fuse_rankings(raw_routes, weights={'dense': .5, 'bm25': .5}, rrf_k=10, top_k=20))
                assert replay(r['raw_query'], raw_pool)[1] == r['arms']['raw']
                assert r['official_baseline'] == old[r['id']]['arms']['baseline']
            else:
                assert r['arms']['raw'] == old[r['id']]['arms']['baseline']
        assert {(s['query'], s['cid']) for s in added} <= used
        report = read(OUT / name / 'report.json')
        assert report['summary'] == {a: {k: sum(r['arms'][a][k] for r in rows)/100 for k in ('recall', 'mrr', 'ndcg')} for a in ('raw', 'standalone', 'joint')}
        reports[name] = {'cases': 100, 'new_ce_pairs': len(added), 'pool_scores_pack_wire_reproduced': True,
                         'raw_route_scope': 'new full-corpus raw ranks; replay validates downstream, not a second dense/BM25 execution' if name == 'MTRAG' else 'matches prior raw ranks'}
    assert sum(r['new_ce_pairs'] for r in reports.values()) <= 8000
    (OUT / 'audit.json').write_text(json.dumps(reports, indent=2) + '\n')
    print(json.dumps(reports, indent=2))

if __name__ == '__main__':
    main()
