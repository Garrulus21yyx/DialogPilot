"""Frozen development ranks: candidate membership only, no model/API calls."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection, ranked, complete
from mcp.rank_fusion import fuse_rankings


def balanced_membership(routes, budget):
    """Round-robin unique candidates; exhausted routes yield remaining slots."""
    selected = []
    offsets = [0] * len(routes)
    while len(selected) < budget:
        changed = False
        for i, route in enumerate(routes):
            while offsets[i] < len(route) and route[offsets[i]] in selected:
                offsets[i] += 1
            if offsets[i] < len(route) and len(selected) < budget:
                selected.append(route[offsets[i]])
                offsets[i] += 1
                changed = True
        if not changed:
            break
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(exist_ok=False)
    source = Path('artifacts/eval/rag-local-parent-pair-v2-2026-09-07/scores.npz')
    snapshot = json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory(prefix='rag-membership-') as temp:
        for name, content in snapshot.items():
            Path(temp, name).write_text(content)
        ds = RagDataset.load(Path(temp), verify_checksum=True)
    cases = ds.select_cases('dev')
    _, chunks, _ = projection(ds.documents, cases, 512, 64, 'structure_aware')
    ids = [c.chunk_id for c in chunks]
    byid = {c.chunk_id: c for c in chunks}
    hits = {c.chunk_id: dict(document_id=c.document_id, source_start_char=c.start_char, source_end_char=c.end_char) for c in chunks}
    scores = np.load(source)
    assert scores['dense'].shape == scores['lexical'].shape == (len(cases), len(ids))
    rows = []
    for i, case in enumerate(cases):
        d = ranked(scores['dense'][i], ids, 20)
        lex = scores['lexical'][i]
        b = tuple(cid for cid in ranked(lex, ids, len(ids)) if lex[ids.index(cid)] > 0)[:20]
        routes = {'dense': d, 'bm25': b}
        allrank = fuse_rankings(routes, weights={'dense': .25, 'bm25': .75}, rrf_k=10, top_k=40)
        members = set(balanced_membership([d, b], 20))
        arms = {'baseline': allrank[:20], 'balanced': tuple(cid for cid in allrank if cid in members)}
        rows.append({'case_id': case.case_id, **{arm: {'ids': order, 'complete': bool(complete(case, order, byid)), 'metrics': evaluate_ranked_hits(case, order, hits, top_k=20)} for arm, order in arms.items()}})
    report = dict(scope='EXPOSED_DEV_LOCAL_CANDIDATES_ONLY_NOT_POSTGRES_OR_ANSWERS', api_calls=0, cases=len(cases), source_k=20, candidate_k=20, dense_weight=.25, rrf_k=10, scores_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), dataset_checksums=ds.manifest,
                  adopted=False, adoption_reason='Final visible evidence and production route-family integration untested',
                  arms={a: dict(complete=sum(r[a]['complete'] for r in rows), mrr20=sum(r[a]['metrics']['mrr'] for r in rows)/len(rows), ndcg20=sum(r[a]['metrics']['ndcg'] for r in rows)/len(rows)) for a in arms},
                  rescues=sum(not r['baseline']['complete'] and r['balanced']['complete'] for r in rows), harms=sum(r['baseline']['complete'] and not r['balanced']['complete'] for r in rows))
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    (output/'cases.jsonl.gz').write_bytes(gzip.compress(''.join(json.dumps(r)+'\n' for r in rows).encode(), mtime=0))
    print(json.dumps(report))

if __name__ == '__main__':
    main()
