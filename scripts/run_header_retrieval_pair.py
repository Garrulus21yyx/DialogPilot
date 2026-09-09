"""Two production-entry chunk strategies, frozen development queries, zero API."""
import asyncio,gc,gzip,hashlib,json,os,subprocess
from dataclasses import asdict
from datetime import datetime,timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote
import uuid
import psycopg
from psycopg import sql
from application.knowledge_retrieval_text import build_child_retrieval_text
from infrastructure.bge_m3_embedding import LocalBGEM3EmbeddingProvider
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from scripts.import_wixqa_cached_postgres import CachedWixVectors
from scripts import run_rag_tool_calibration as calibration
from evaluation import ecommerce_pure_rag

ROOT=Path('artifacts/eval/header-retrieval-pair40-2026-09-09-v3')
CORPUS=Path('artifacts/eval/ecommerce-complex-v2-scoped-corpus-v2/corpus.json')
INPUTS=Path('data/eval/ecommerce-complex-v2/dev.inputs.json')
REWRITE=Path('artifacts/eval/ecommerce-complex-v2-dev/runtime/pure-cases.jsonl.gz')
MODEL=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181')

class CachedProvider:
    def __init__(self,config):
        self.live=LocalBGEM3EmbeddingProvider(config);self.profile=self.live.profile
        cached=CachedWixVectors(Path('/tmp/dialogpilot-wixqa-full-index-20260908'))
        assert self.profile==cached.profile
        self.docs=cached.vectors;self.queries={};self.counts={'new_documents':0,'document_hits':0,'new_queries':0,'query_hits':0}
    def encode(self,texts,kind):
        cache=self.docs if kind=='document' else self.queries
        missing=list(dict.fromkeys(t for t in texts if t not in cache))
        if missing:
            fn=self.live.embed_documents if kind=='document' else self.live.embed_queries
            for t,v in zip(missing,fn(missing),strict=True):cache[t]=list(v)
        self.counts['new_documents' if kind=='document' else 'new_queries']+=len(missing)
        self.counts[kind+'_hits']+=len(texts)-len(missing)
        return [cache[t] for t in texts]
    def embed_documents(self,texts):return self.encode(texts,'document')
    def embed_queries(self,texts):return self.encode(texts,'query')

async def main():
    ROOT.mkdir(parents=True,exist_ok=False)
    os.environ.update(PGOPTIONS='-c max_parallel_maintenance_workers=0',MODEL_PROVIDER='deepseek',RAG_RERANKER='local_bge',RAG_LOCAL_RERANKER_PATH='/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3',RAG_LOCAL_RERANKER_DEVICE='cuda',HF_HUB_OFFLINE='1',KNOWLEDGE_FILTER_CATALOG_FILE=str(Path('artifacts/eval/ecommerce-complex-v2-scoped-corpus-v2/catalog.json').resolve()))
    paths=[CORPUS,INPUTS,REWRITE,Path(__file__),Path('mcp/document_chunker.py'),Path('infrastructure/hybrid_retrieval_backend.py')]
    (ROOT/'manifest.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'api_cap':0,'sql_timeout_ms':5000,'quality_only':True,'cases':40,'fixed_as_of':'2026-09-10T00:00:00+00:00','hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}},indent=2)+'\n')
    provider=None
    def factory(config):
        nonlocal provider
        if provider is None:provider=CachedProvider(config)
        return provider
    config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
    env=dict(x.split('=',1) for x in config['Config']['Env'] if '=' in x)
    base='postgresql://'+quote(env['POSTGRES_USER'],safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'
    with patch.object(calibration,'RetrievalPoolConfig',partial(calibration.RetrievalPoolConfig,statement_timeout_ms=5000)), patch.object(calibration,'LocalBGEM3EmbeddingProvider',factory), patch.object(ecommerce_pure_rag,'run',partial(ecommerce_pure_rag.run,only_scope_filtered=True,as_of=datetime(2026,9,10,tzinfo=timezone.utc))):
        for strategy in ['structure_aware','markdown_headers']:
            out=ROOT/strategy;out.mkdir()
            name='dialogpilot_headers_'+uuid.uuid4().hex[:10]
            args=SimpleNamespace(candidate_scope_probe=False,model=MODEL,scenario='basic',corpus_file=CORPUS,distractors=None,
                output=out,pure_rag_inputs=INPUTS,pure_rewrite_seed=None,pure_rewrite_cache=REWRITE,pure_scope_pair=True,
                max_api_calls=0,mixed_business=False,full_chain=False)
            before=dict(provider.counts) if provider else {}
            with psycopg.connect(base+'postgres',autocommit=True) as admin:
                admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
                try:
                    with patch.object(calibration,'PostgresKnowledgeStore',partial(PostgresKnowledgeStore,chunk_strategy=strategy)):
                        await calibration.evaluate(args,base+name)
                finally:admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            (out/'embedding-counts.json').write_text(json.dumps({k:v-before.get(k,0) for k,v in provider.counts.items()},indent=2)+'\n')
            print('ARM COMPLETE',strategy,provider.counts,flush=True)
            gc.collect()
    (ROOT/'completion.json').write_text(json.dumps({'arms':2,'cases_per_arm':40,'api_calls':0,'embedding_counts':provider.counts},indent=2)+'\n')
if __name__=='__main__':asyncio.run(main())
