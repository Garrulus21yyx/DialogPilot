"""Replay frozen retrieval losses; no new inference and no policy selection."""
import gzip
import json
import hashlib
from collections import Counter
from pathlib import Path

from scripts.audit_rag_fresh100 import data
from scripts.replay_rag_rank_selection import read, digest, pack
from scripts.run_rag_fresh100 import ROOT
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_provider_free import projection, complete
from mcp.rank_fusion import fuse_rankings

OUT = Path('artifacts/eval/rag-fresh100-stages-2026-09-08')


def main():
    selection = read(ROOT / 'selection.json')
    OUT.mkdir(exist_ok=True)
    summaries = {}
    for name in ('Doc2Dial', 'MTRAG', 'WixQA'):
        rows = read(ROOT / name / 'cases.json.gz')
        scores = {(s['case_id'], s['cid']): s for s in read(ROOT / name / 'scores.json.gz')}
        src, texts, metric, expected = data(name, rows, selection)
        if name == 'Doc2Dial':
            ds = RagDataset.load(ROOT / 'doc2dial', verify_checksum=True)
            _, chunks, _ = projection(ds.documents, ds.cases, 512, 64, 'structure_aware')
            byid = {c.chunk_id: c for c in chunks}
            cases = {c.case_id: c for c in ds.cases}
            def recall(row, ids):
                return float(complete(cases[row['id']], ids, byid))
            containment = sum(complete(c, list(byid), byid) for c in ds.cases) / len(ds.cases)
        else:
            cases = {c['id']: c for c in selection[name]}
            def gold(row):
                c = cases[row['id']]
                return set(c['article_ids']) if name == 'WixQA' else {e['document_id'] for e in c['evidence']}
            def recall(row, ids):
                found = {src[cid].document_id for cid in ids} if name == 'WixQA' else set(ids)
                return len(found & gold(row)) / len(gold(row))
            containment = None
        records = []
        for row in rows:
            assert (row['query'], row['group']) == expected[row['id']]
            union = list(fuse_rankings(row['routes'], weights={'dense': .5, 'bm25': .5}, rrf_k=10, top_k=40))
            base = union[:20]
            assert row['pool'] == (base if name == 'MTRAG' else union)
            lookup = {}
            for cid in row['pool']:
                score = scores[row['id'], cid]
                assert score['query'] == row['query']
                assert score['text_sha256'] == hashlib.sha256(texts[cid].encode()).hexdigest()
                lookup[cid] = score['score']
            tie = (lambda cid: row['pool'].index(cid)) if name == 'WixQA' else (lambda cid: cid)
            ce = sorted(base, key=lambda cid: (-lookup[cid], tie(cid)))
            ids, tokens = pack(row['query'], ce, src)
            assert {**metric(row, ids), 'ids': ids, 'tokens': tokens} == row['arms']['baseline']
            stages = {'dense20': row['routes']['dense'], 'bm25_20': row['routes']['bm25'],
                      'union40': union, 'fusion20': base, 'ce5': ce[:5], 'wire5': ids}
            values = {stage: recall(row, candidates) for stage, candidates in stages.items()}
            assert values['union40'] >= max(values['dense20'], values['bm25_20'], values['fusion20'])
            assert values['fusion20'] >= max(values['ce5'], values['wire5'])
            assert abs(values['wire5'] - row['arms']['baseline']['recall']) < 1e-12
            loss = {'outside_route_union': 1 - values['union40'],
                    'fusion_cut': values['union40'] - values['fusion20'],
                    'top5_selection': values['fusion20'] - values['ce5'],
                    'pack_net': values['ce5'] - values['wire5']}
            assert abs(sum(loss.values()) - (1 - values['wire5'])) < 1e-12
            record = {'id': row['id'], 'query': row['query'], 'recall': values, 'loss': loss,
                      'stages': stages, 'ce_order': ce, 'tokens': tokens,
                      'pack_removed_from_ce5': [x for x in ce[:5] if x not in ids],
                      'pack_added_after_ce5': [x for x in ids if x not in ce[:5]]}
            if name != 'Doc2Dial':
                record['gold_count'] = len(gold(row))
                record['ideal_five_unit_recall_cap'] = min(5, len(gold(row))) / len(gold(row))
            records.append(record)
        summary = {'n': len(records), 'query_mode': {'Doc2Dial': 'raw latest turn; history not used',
                   'MTRAG': 'official rewritten query', 'WixQA': 'original question'}[name],
                   'whole_corpus_complete_containment': containment,
                   'mean_recall': {k: sum(r['recall'][k] for r in records)/len(records) for k in stages},
                   'mean_loss': {k: sum(r['loss'][k] for r in records)/len(records) for k in loss},
                   'pack_changed_cases': sum(r['stages']['ce5'] != r['stages']['wire5'] for r in records),
                   'input_sha256': {f: digest(ROOT/name/f) for f in ('cases.json.gz', 'scores.json.gz')},
                   'all_baseline_scores_and_wire_reproduced': True}
        if name == 'Doc2Dial':
            misses = [r for r in records if r['recall']['union40'] == 0]
            summary['cases_with_history'] = sum(bool(c.history) for c in ds.cases)
            summary['union_incomplete_cases'] = len(misses)
            summary['union_incomplete_with_all_gold_documents_present'] = sum(
                {e.document_id for e in cases[r['id']].evidence}.issubset(
                    {byid[cid].document_id for cid in r['stages']['union40']}) for r in misses)
        if name != 'Doc2Dial':
            summary['gold_count_distribution'] = dict(Counter(r['gold_count'] for r in records))
            summary['mean_ideal_five_unit_recall_cap'] = sum(r['ideal_five_unit_recall_cap'] for r in records)/len(records)
        (OUT / (name+'.json.gz')).write_bytes(gzip.compress(json.dumps(records).encode(), mtime=0))
        summaries[name] = summary
    report = {'api_calls': 0, 'new_model_inferences': 0, 'datasets': summaries,
              'scope': 'Operational loss attribution, not causal proof of semantic failure. Top5 loss includes finite output budget. Pack loss is signed net change. Consumed data; no policy adopted.'}
    (OUT/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
