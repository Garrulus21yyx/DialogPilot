import gzip
import json
import math
from pathlib import Path

ROOT=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08')


def test_packing_wire_metrics_and_budget():
    rows=json.loads(gzip.decompress((ROOT/'cases.json.gz').read_bytes()))
    report=json.loads((ROOT/'report.json').read_text())
    assert len(rows)==32
    computed={a:[] for a in ('0.25','0.5','0.75')}
    for row in rows:
        gold=set(row['gold'])
        for arm,value in row['arms'].items():
            ids=value['packed_ids']
            assert len(ids)==len(set(ids))<=5
            assert value['body_estimated_tokens']<=2600
            evidence=json.loads(value['wire'])['evidence']
            assert [e['source']['source_id'] for e in evidence]==ids==value['serialized_ids']
            assert not set(ids)&set(value['skipped_budget'])
            for e in evidence:
                import hashlib
                assert e['source']['end_char']-e['source']['start_char']==len(e['text'])
                assert hashlib.sha256(e['text'].encode()).hexdigest()==e['source']['checksum']
            relevance=[int(p in gold) for p in ids]
            expected={'recall@5':sum(relevance)/len(gold),'mrr@5':next((1/(i+1) for i,x in enumerate(relevance) if x),0.),'ndcg@5':sum(x/math.log2(i+2) for i,x in enumerate(relevance))/sum(1/math.log2(i+2) for i in range(min(5,len(gold))))}
            assert expected==value['metrics']
            computed[arm].append(expected)
    for arm,values in computed.items():
        assert report['summary'][arm]=={k:sum(v[k] for v in values)/32 for k in values[0]}
