"""Audit G1 saved ranking evidence without importing or querying a database."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from scripts.run_rag_known_miss_replay import positions, reconstruct_fusion


def audit(root):
    snapshots=json.loads(gzip.decompress((root/'input-datasets.json.gz').read_bytes()))
    datasets={}
    for name,files in snapshots.items():
        manifest=json.loads(files['manifest.json'])
        for field,file in [('cases_sha256','cases.jsonl'),('corpus_sha256','corpus.jsonl')]:
            assert hashlib.sha256(files[file].encode()).hexdigest()==manifest[field]
        datasets[name]=({r['id']:r for r in map(json.loads,files['cases.jsonl'].splitlines())},
                        {r['id']:r['content'] for r in map(json.loads,files['corpus.jsonl'].splitlines())})
    records=[json.loads(x) for x in gzip.decompress((root/'cases.jsonl.gz').read_bytes()).splitlines()]
    out=[]
    for row in records:
        cases,docs=datasets[row['dataset']]
        case=SimpleNamespace(evidence=[SimpleNamespace(**e) for e in cases[row['case_id']]['evidence']])
        for chunk in row['deep']:
            assert chunk['content'].strip()==docs[chunk['source_id']][chunk['source_start_char']:chunk['source_end_char']].strip()
        order=reconstruct_fusion(row['deep'])
        for status,key in [('candidate_status','candidates'),('relaxed_status','relaxed_candidates')]:
            if row[status]=='OK':assert list(order)==[r['chunk_id'] for r in row[key]]
        out.append(dict(case_id=row['case_id'],mode=row['mode'],reconstructed_fused20=positions(case,row['deep'],order)))
    return dict(api_calls=0,input_checksums_verified=True,all_available_pg_orders_match=True,source_offsets_verified=True,cases=out)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);args=p.parse_args()
    print(json.dumps(audit(args.root),ensure_ascii=False,indent=2))
