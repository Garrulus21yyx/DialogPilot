import gzip,json
from pathlib import Path


def test_focus_changes_only_system_and_keeps_four_inputs():
    root=Path('artifacts/eval')
    def load(name):return [json.loads(l) for l in gzip.decompress((root/name/'results.jsonl.gz').read_bytes()).splitlines()]
    old=load('rag-g4-verifier4-2026-09-08');new=load('rag-g4-verifier-focus4-2026-09-08')
    assert len(old)==len(new)==4
    for a,b in zip(old,new):
        assert (a['case_id'],a['arm'],a['answer'])==(b['case_id'],b['arm'],b['answer'])
        x,y=a['calls'][0]['request'],b['calls'][0]['request']
        assert x['messages']==y['messages'] and x['tools']==y['tools']
        assert y['system'].startswith(x['system']) and len(y['system'])>len(x['system'])
    clauses=load('rag-g4-verifier-clause4-2026-09-08')
    assert len(clauses)==4
    for r in clauses:
        assert len(r['calls'])==1 and not r['calls'][0].get('error_type')
        assert r['result']['assessment'] is not None
