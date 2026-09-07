"""G1 known failures: fixed PG retrieval, deep ranks for diagnosis only, zero API."""
import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import uuid

import psycopg
from psycopg import sql

from application.knowledge_retriever import KnowledgeRetrievalRequest
from application.cost_budget_policy import OFFLINE_KNOWLEDGE_INGEST_BUDGET
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.local_bge_m3_retrieval_eval import deterministic_query
from evaluation.postgres_rag_fusion_capture import build_source_capture_policy
from infrastructure.bge_m3_embedding import BGEM3EmbeddingConfig, LocalBGEM3EmbeddingProvider
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.retrieval_postgres import RetrievalPostgresPool, RetrievalPoolConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from mcp.source_document import SourceDocument
from dataclasses import replace


RESOLVED = {
 'doc2dial-heldout-399568993908018be3e04501922686ee-13': 'The user reports active duty service and combat service after November 11, 1998, but answers no to being discharged or released on or after January 29, 2003. What VA health care eligibility rules apply to this situation?',
 'doc2dial-heldout-83c939393b970a0bcf0a9e0a71196eb5-11': 'As a noncitizen adult foreign worker, what documents do I need for a replacement card and proof that I am authorized to work in the United States?',
 'doc2dial-heldout-aadc9fd5710361ed8a3551e540c608d8-11': 'What are Social Security Disability Benefits? I have been asking about eligibility, required documents, and applying from outside the United States.',
 'doc2dial-heldout-af80457aacaac2dcdee2abcfe3d12144-11': 'How do I apply for a Board Appeal of a contested VA benefits claim when multiple people claim the same benefit, and list the issues and decision dates?',
}
CROSS_ID = 'doc2dial-dev-3231fc98c27c1216a21ec9baf5abd0ea-11'


def positions(case, rows, order):
    by={r['chunk_id']:r for r in rows}
    gold_ranks=[]
    for gold in case.evidence:
        gold_ranks.append(next((i for i,key in enumerate(order,1)
            if by[key]['source_id']==gold.document_id and by[key]['source_start_char']<=gold.start_char
            and by[key]['source_end_char']>=gold.end_char),None))
    docs={g.document_id for g in case.evidence}
    return dict(evidence_ranks=gold_ranks,all_evidence_rank=max(gold_ranks) if all(x is not None for x in gold_ranks) else None,
                first_gold_document_rank=next((i for i,key in enumerate(order,1) if by[key]['source_id'] in docs),None))


def reconstruct_fusion(rows):
    """Replay fixed source Top20 from deep diagnostics, never promote depth1000."""
    from mcp.rank_fusion import fuse_rankings
    routes={route:tuple(r['chunk_id'] for r in sorted(rows,key=lambda x:x['ranks'].get(route,100000))
                        if r['ranks'].get(route,100000)<=20)
            for route in ('raw:vector','raw:bm25')}
    return fuse_rankings(routes,weights={'raw:vector':.25,'raw:bm25':.75},rrf_k=10,top_k=20)


async def run_dataset(args, root, entries, embedding, dburl):
    ds=RagDataset.load(root,verify_checksum=True); cases={c.case_id:c for c in ds.cases}
    PostgresMigrationRunner(dburl).upgrade()
    pool=PostgresPool(PostgresPoolConfig(dburl,min_size=1,max_size=3));pool.open()
    rp=RetrievalPostgresPool(RetrievalPoolConfig(dburl,min_size=1,max_size=3));rp.open()
    diagnostic_pool=RetrievalPostgresPool(RetrievalPoolConfig(dburl,min_size=1,max_size=3,statement_timeout_ms=10000));diagnostic_pool.open()
    try:
        store=PostgresKnowledgeStore(pool,tenant_id='rag-g1',embedding_provider=embedding)
        docs=[SourceDocument.create(source_id=d.document_id,title=d.title,content=d.content,source_type='text',effective_from=datetime(2025,1,1,tzinfo=timezone.utc)) for d in ds.documents]
        n=OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_sources_per_batch
        for i in range(0,len(docs),n):
            store.import_documents(docs[i:i+n])
            print('IMPORTED',root.name,min(i+n,len(docs)),len(docs),flush=True)
        generation=store.active_generation()
        errors=[]
        backend=PostgresHybridBackend(rp)
        class ObservedBackend:
            def retrieve(self,request):
                try:return backend.retrieve(request)
                except Exception as exc:
                    cause=exc.__cause__ or exc
                    errors.append(dict(owner='backend',type=type(exc).__name__,cause=type(cause).__name__,sqlstate=getattr(cause,'sqlstate',None),message=getattr(getattr(cause,'diag',None),'message_primary',None)))
                    raise
        def observed_embed(query,generation):
            try:return store.embed_query(query,generation)
            except Exception as exc:
                errors.append(dict(owner='embedding',type=type(exc).__name__))
                raise
        source=PostgresKnowledgeCandidateSource(backend=ObservedBackend(),generations=store._generations,pool=rp,embed_query=observed_embed)
        diagnostic_source=PostgresKnowledgeCandidateSource(backend=PostgresHybridBackend(diagnostic_pool),generations=store._generations,pool=diagnostic_pool,embed_query=observed_embed)
        policy=replace(build_source_capture_policy(generation),dense_weight=.25,lexical_weight=.75,candidate_k=20)
        manifest=dict(dataset=ds.manifest,generation=asdict(generation),policy=asdict(policy),entries=entries,
                      scope='current PG candidate source; no Agent, rerank, pack or generation; deep pool diagnosis only',api_calls=0,diagnostic_depth=1000,
                      production_statement_timeout_ms=750,diagnostic_statement_timeout_ms=10000,diagnostic_budget_is_not_production_fix=True)
        (args.output/(root.name+'-manifest.json')).write_text(json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
        records=[]
        for case_id,mode,query in entries:
            errors.clear()
            case=cases[case_id]
            request=KnowledgeRetrievalRequest(tenant_id='rag-g1',user_scope='public-evaluation',authorization_fingerprint='isolated-g1',acl_policy_fingerprint='public-only',deletion_epoch=0,
                requirement_signature='knowledge.known_miss',query=query,history=(),conversation_range_hash=hashlib.sha256(case_id.encode()).hexdigest(),locale='zh-CN',product=None,
                manifest_fingerprint=generation.manifest_hash,generation_id=generation.generation_id,policy=policy,query_mode='RESOLVED',as_of=datetime(2026,9,7,tzinfo=timezone.utc))
            normal=await source.search_variants_async(request,[('raw',query,1.)],top_k=20)
            relaxed=await diagnostic_source.search_variants_async(request,[('raw',query,1.)],top_k=20)
            deep=await diagnostic_source.capture_source_rankings_async(request,[('raw',query,1.)],dense_k=1000,lexical_k=1000)
            row=dict(case_id=case_id,mode=mode,query=query,history=case.history,raw_query=case.query,dataset=root.name,
                     candidate_status=normal.status.value,candidate_detail=normal.detail_code,relaxed_status=relaxed.status.value,relaxed_candidates=list(relaxed.candidates),diagnostic_status=deep.status.value,diagnostic_detail=deep.detail_code,errors=list(errors),candidates=list(normal.candidates),deep=list(deep.candidates),rankings={})
            for label,result in [('production_fused20',normal),('relaxed_diagnostic_fused20',relaxed)]:
                if result.status.value=='OK':row['rankings'][label]=positions(case,result.candidates,[r['chunk_id'] for r in result.candidates])
            if deep.status.value=='OK':
                for route in sorted({k for r in deep.candidates for k in r['ranks']}):
                    ordered=sorted([r for r in deep.candidates if route in r['ranks']],key=lambda r:r['ranks'][route])
                    row['rankings'][route]=positions(case,ordered,[r['chunk_id'] for r in ordered])
                    for doc_id in sorted({g.document_id for g in case.evidence}):
                        inside=[r for r in ordered if r['source_id']==doc_id]
                        row['rankings'][route+'::within::'+doc_id]=positions(case,inside,[r['chunk_id'] for r in inside])
            records.append(row)
            print('CASE',case_id,mode,row['rankings'],row['candidate_detail'],row['errors'],flush=True)
        return records
    finally:diagnostic_pool.close();rp.close();pool.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--embedding',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    old=Path('artifacts/eval/doc2dial-rag-en-heldout-fresh-local-v1'); new=Path('artifacts/eval/doc2dial-rag-mini-dev-v1')
    cs={c.case_id:c for c in RagDataset.load(old).cases}
    entries=[(key,mode,deterministic_query(cs[key],'user_history') if mode=='historical_expression' else value) for key,value in RESOLVED.items() for mode in ['historical_expression','context_resolved']]
    saved=[json.loads(s) for s in gzip.decompress(Path('artifacts/eval/rag-authored-query20-2026-09-07/queries.jsonl.gz').read_bytes()).splitlines()]
    cross=next(r['resolved_queries'][0] for r in saved if r['case_id']==CROSS_ID)
    (args.output/'frozen-queries.json').write_text(json.dumps(entries+[(CROSS_ID,'frozen_cross_language',cross)],ensure_ascii=False,indent=2))
    with (args.embedding/'pytorch_model.bin').open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
    embedding=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(args.embedding,args.embedding.name,sha,device='cuda',batch_size=8))
    base=os.environ['TEST_DATABASE_URL'];parts=urlsplit(base);records=[]
    for root,selected in [(old,entries),(new,[(CROSS_ID,'frozen_cross_language',cross)])]:
        name='dialogpilot_rag_g1_'+uuid.uuid4().hex[:12]
        with psycopg.connect(base,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        try:records.extend(asyncio.run(run_dataset(args,root,selected,embedding,urlunsplit((parts.scheme,parts.netloc,'/'+name,parts.query,parts.fragment)))))
        finally:
            with psycopg.connect(base,autocommit=True) as c:
                c.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()',(name,))
                c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
    raw=''.join(json.dumps(r,ensure_ascii=False,default=str)+'\n' for r in records).encode()
    (args.output/'cases.jsonl.gz').write_bytes(gzip.compress(raw,mtime=0))
    summary=[{k:v for k,v in r.items() if k not in ['candidates','relaxed_candidates','deep','history']} for r in records]
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
