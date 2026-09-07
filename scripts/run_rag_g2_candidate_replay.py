"""Replay actual captured Agent queries at fixed production candidate settings."""
import argparse
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit
import uuid
import psycopg
from psycopg import sql
from scripts.run_rag_known_miss_replay import run_dataset
from infrastructure.bge_m3_embedding import BGEM3EmbeddingConfig, LocalBGEM3EmbeddingProvider


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--embedding',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(exist_ok=False,parents=True)
    captures=[json.loads(line) for line in gzip.decompress(Path('artifacts/eval/rag-g2-context-miss2-2026-09-07/queries.jsonl.gz').read_bytes()).splitlines()]
    entries=[];excluded=[]
    for row in captures:
        entries.append((row['case_id'],'raw',row['raw_query']))
        if len(row['resolved_queries'])==1:
            entries.append((row['case_id'],'actual_agent',row['resolved_queries'][0]))
        else:
            excluded.append(dict(case_id=row['case_id'],reason='NO_KNOWLEDGE_QUERY',route=row['plan']['route']['mode']))
    snapshot=json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    with (args.embedding/'pytorch_model.bin').open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    embedding=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(args.embedding,args.embedding.name,digest,device='cuda',batch_size=8))
    base=os.environ['TEST_DATABASE_URL'];parts=urlsplit(base);name='rag_g2_candidates_'+uuid.uuid4().hex[:10]
    with psycopg.connect(base,autocommit=True) as conn:conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with TemporaryDirectory() as temp:
            root=Path(temp)/'doc2dial-rag-mini-dev-v1';root.mkdir()
            for filename,content in snapshot.items():(root/filename).write_text(content)
            rows=asyncio.run(run_dataset(args,root,entries,embedding,urlunsplit((parts.scheme,parts.netloc,'/'+name,parts.query,parts.fragment))))
        (args.output/'cases.jsonl.gz').write_bytes(gzip.compress(''.join(json.dumps(r,default=str)+'\n' for r in rows).encode(),mtime=0))
        (args.output/'summary.json').write_text(json.dumps(dict(api_calls=0,excluded=excluded,rows=[{k:r[k] for k in ('case_id','mode','query','candidate_status','rankings')} for r in rows]),indent=2)+'\n')
    finally:
        with psycopg.connect(base,autocommit=True) as conn:
            conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))

if __name__=='__main__':main()
