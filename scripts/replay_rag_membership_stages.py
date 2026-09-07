"""Frozen candidate membership -> local CE -> production context packer.

Restores input snapshot and audits the cached scoring recipe before reuse.
No API calls, no production policy changes, no Agent or answer generation.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

import numpy as np
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection, ranked, complete
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.rank_fusion import fuse_rankings


def read_rows(path):
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--added-scores', type=Path, help='Replay saved missing-pair scores without loading a model')
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False, parents=True)
    old = Path('artifacts/eval/rag-local-selection-stages-2026-09-07')
    membership = Path('artifacts/eval/rag-g3-membership-dev300-2026-09-07')
    snapshot = json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory() as temp:
        for name, content in snapshot.items():
            Path(temp, name).write_text(content)
        ds = RagDataset.load(Path(temp), verify_checksum=True)
    cases = ds.select_cases('dev')
    _, chunks, texts = projection(ds.documents, cases, 512, 64, 'structure_aware')
    ids = [c.chunk_id for c in chunks]
    positions = {cid: i for i, cid in enumerate(ids)}
    byid = {c.chunk_id: c for c in chunks}
    hitmap = {c.chunk_id: dict(document_id=c.document_id, source_start_char=c.start_char, source_end_char=c.end_char) for c in chunks}
    rows = read_rows(membership/'cases.jsonl.gz')
    manifest = json.loads((old/'manifest.json').read_text())
    assert manifest['dataset'] == ds.manifest
    assert [r['case_id'] for r in rows] == [c.case_id for c in cases]
    scores_path = Path('artifacts/eval/rag-local-parent-pair-v2-2026-09-07/scores.npz')
    assert hashlib.sha256(scores_path.read_bytes()).hexdigest() == json.loads((membership/'report.json').read_text())['scores_sha256']
    scores = np.load(scores_path)
    routes = []
    for i, case in enumerate(cases):
        lex = scores['lexical'][i]
        route = {'dense': ranked(scores['dense'][i], ids, 40), 'bm25': tuple(cid for cid in ranked(lex, ids, len(ids)) if lex[positions[cid]] > 0)[:40]}
        reproduced = fuse_rankings(route, weights={'dense': .5, 'bm25': .5}, rrf_k=10, top_k=20)
        assert list(reproduced) == manifest['candidate_orders'][i]
        routes.append({name: rank[:20] for name, rank in route.items()})
    model_path = Path(manifest['reranker'])
    assert hashlib.sha256((model_path/'model.safetensors').read_bytes()).hexdigest() == manifest['reranker_model_sha256']
    old_locations = [(i, cid) for i, rank in enumerate(manifest['candidate_orders']) for cid in rank]
    values = np.load(old/'crossencoder-scores.npz')['values']
    assert len(old_locations) == len(values) == 6000
    ce = dict(zip(old_locations, map(float, values)))
    needed = sorted({(i, cid) for i, r in enumerate(rows) for arm in ('baseline', 'balanced') for cid in r[arm]['ids']})
    missing = [loc for loc in needed if loc not in ce]
    assert len(missing) <= 2360
    start = time.monotonic()
    added = []
    if args.added_scores:
        added = json.loads(gzip.decompress(args.added_scores.read_bytes()))
        restored = {(r['case_index'], r['candidate_id']): r['score'] for r in added}
        assert len(restored) == len(added) and set(restored) == set(missing)
        assert all(np.isfinite(v) for v in restored.values())
        ce.update(restored)
    else:
        import torch
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True, torch_dtype=torch.float16).to('cuda').eval()
        for offset in range(0, len(missing), 4):
            batch = missing[offset:offset+4]
            encoded = tok([cases[i].query for i, cid in batch], [texts[positions[cid]] for i, cid in batch], padding=True, truncation=False, return_tensors='pt')
            assert encoded['input_ids'].shape[1] <= 8192
            with torch.inference_mode():
                logits = model(**{k: v.to('cuda') for k, v in encoded.items()}).logits.view(-1).float().cpu().tolist()
            for loc, value in zip(batch, logits):
                ce[loc] = value
                added.append(dict(case_index=loc[0], candidate_id=loc[1], score=value))
            if offset % 400 == 0:
                print('LOCAL_MISSING', offset, '/', len(missing), flush=True)
    (args.output/'added-scores.json.gz').write_bytes(gzip.compress(json.dumps(added).encode(), mtime=0))
    results = []
    for i, (case, row) in enumerate(zip(cases, rows)):
        record = dict(case_id=case.case_id, query=case.query, arms={})
        for arm in ('baseline', 'balanced'):
            candidate = row[arm]['ids']
            ordered = sorted(candidate, key=lambda cid: (-ce[(i, cid)], cid))
            packed = ContextPacker().pack([ContextCandidate(cid, byid[cid].document_id, byid[cid].content, byid[cid].start_char, byid[cid].end_char, score=1/(n+1)) for n, cid in enumerate(ordered[:5])], max_tokens=2600, max_chunks=5)
            visible = all(any(c.document_id == e.document_id and c.start_char <= e.start_char and c.end_char >= e.end_char for c in packed.selected) for e in case.evidence)
            assert packed.token_count <= 2600
            record['arms'][arm] = dict(candidate_complete=row[arm]['complete'], rerank_complete=bool(complete(case, ordered[:5], byid)), packed_complete=visible, metrics=evaluate_ranked_hits(case, ordered, hitmap, top_k=5), tokens=packed.token_count, reranked_ids=ordered, packed_ids=[c.chunk_id for c in packed.selected])
        if row['baseline']['complete'] != row['balanced']['complete']:
            removed = set(row['baseline']['ids']) - set(row['balanced']['ids'])
            added_ids = set(row['balanced']['ids']) - set(row['baseline']['ids'])
            witnesses = []
            for evidence in case.evidence:
                covers = [cid for cid, c in byid.items() if c.document_id == evidence.document_id and c.start_char <= evidence.start_char and c.end_char >= evidence.end_char]
                witnesses.append(dict(document_id=evidence.document_id, span=[evidence.start_char, evidence.end_char], covering_ids=covers, removed_gold=sorted(removed.intersection(covers)), added_gold=sorted(added_ids.intersection(covers)), route_gold_ranks={name: [rank.index(cid)+1 for cid in covers if cid in rank] for name, rank in routes[i].items()}))
            record['candidate_change'] = dict(kind='rescue' if row['balanced']['complete'] else 'harm', witnesses=witnesses)
        results.append(record)
    summary = dict(scope='EXPOSED_DEV_LOCAL_CE_AND_PACKER_NOT_TOOLMESSAGE_OR_ANSWERS', cases=len(cases), api_calls=0, reused_pairs=len(needed)-len(missing), added_pairs=len(missing), scoring_and_packing_wall_ms=(time.monotonic()-start)*1000, new_model_pairs=0 if args.added_scores else len(missing), adopted=False, arms={}, paired={})
    for arm in ('baseline', 'balanced'):
        summary['arms'][arm] = {metric: sum(r['arms'][arm][metric] for r in results) for metric in ('candidate_complete','rerank_complete','packed_complete')}
        summary['arms'][arm].update({metric+'5': sum(r['arms'][arm]['metrics'][metric] for r in results)/len(results) for metric in ('mrr','ndcg')})
    for stage in ('candidate_complete','rerank_complete','packed_complete'):
        summary['paired'][stage] = dict(rescues=sum(not r['arms']['baseline'][stage] and r['arms']['balanced'][stage] for r in results), harms=sum(r['arms']['baseline'][stage] and not r['arms']['balanced'][stage] for r in results))
    summary['audit'] = dict(dataset_checksums=ds.manifest, model_sha256=manifest['reranker_model_sha256'], recipe='Reconstructed old raw-query/structure512-64/full-input FP16 batch4 scoring recipe and all 6000 candidate locations; historical token tensors not saved', current_input_sha256=hashlib.sha256(json.dumps({'queries':[c.query for c in cases], 'texts':texts}, ensure_ascii=False).encode()).hexdigest())
    (args.output/'cases.jsonl.gz').write_bytes(gzip.compress(''.join(json.dumps(r)+'\n' for r in results).encode(), mtime=0))
    (args.output/'report.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary), flush=True)

if __name__ == '__main__':
    main()
