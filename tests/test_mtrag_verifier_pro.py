import gzip,json
from pathlib import Path


def test_model_pair_keeps_actual_input_and_schema_fixed():
    root=Path('artifacts/eval')
    def load(name):return [json.loads(l) for l in gzip.decompress((root/name/'results.jsonl.gz').read_bytes()).splitlines()]
    flash=load('rag-g4-verifier-focus4-2026-09-08');pro=load('rag-g4-verifier-pro4-2026-09-08')
    assert len(flash)==len(pro)==4
    for x,y in zip(flash,pro):
        assert (x['case_id'],x['arm'],x['answer'])==(y['case_id'],y['arm'],y['answer'])
        a,b=x['calls'][0]['request'],y['calls'][0]['request']
        for k in ('messages','system','tools'):assert a[k]==b[k]
        assert not y['calls'][0].get('error_type')
