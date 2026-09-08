import gzip,hashlib,json
from pathlib import Path
from scripts.run_mtrag_lexical_query_pair import metrics
from scripts.run_mtrag_reranker_pair import at5

ROOT=Path('artifacts/eval/rag-g4-mtrag-heldout35-2026-09-08')

def test_heldout_identity_selection_and_fixed_metrics():
    splitpath=Path('artifacts/eval/rag-three-dataset-lock-v3-2026-09-07/mtrag-split.json')
    split=json.loads(splitpath.read_text());selection=json.loads((ROOT/'selection.json').read_text())
    assert selection['source_split_sha256']==hashlib.sha256(splitpath.read_bytes()).hexdigest()
    groups={g['conversation_id']:g for g in split['groups'] if g['split']=='heldout'}
    assert len(selection['cases'])==len(groups)==35
    assert len({r['group_id'] for r in selection['cases']})==35
    for r in selection['cases']:
        group=groups[r['group_id'].removeprefix('mtrag-')]
        expected=min(group['task_ids'],key=lambda x:hashlib.sha256(('mtrag-heldout35-v1\0'+x).encode()).hexdigest())
        assert r['case_id']==expected
    load=lambda name:json.loads(gzip.decompress((ROOT/name).read_bytes()))
    rows=load('retrieval/cases.json.gz');packed=load('pack/cases.json.gz')
    assert {r['case_id'] for r in rows}=={r['case_id'] for r in selection['cases']}
    for r in rows:
        assert set(r['fusion'])=={'0.25','0.75'}
        for v in r['fusion'].values():
            assert len(v['ranking'])<=20
            assert v['metrics']==metrics(v['ranking'],set(r['gold']))
    for r in packed:
        for v in r['arms'].values():
            assert v['body_estimated_tokens']<=2600 and len(v['packed_ids'])<=5
            assert v['metrics']==at5(v['packed_ids'],set(r['gold']))
            assert v['serialized_ids']==v['packed_ids']
