"""Import full WixQA via the production store using exact cached model vectors."""
import gzip,hashlib,json,subprocess,time
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import quote
import numpy as np
import psycopg
from psycopg import sql
from application.hybrid_retrieval import EmbeddingProfile,EmbeddingProviderKind
from application.knowledge_retrieval_text import build_child_retrieval_text
from infrastructure.bge_m3_embedding import BGE_M3_PROVIDER_ID,BGE_M3_MODEL_ID,BGE_M3_PREPROCESSING
from infrastructure.postgres import PostgresMigrationRunner,PostgresPool,PostgresPoolConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from mcp.source_document import SourceDocument
from scripts.prepare_wixqa_local_index import digest


class CachedWixVectors:
    def __init__(self, cache):
        complete=json.loads((cache/'COMPLETE.json').read_text())
        assert digest(cache/'identity.json')==complete['identity_sha256']
        assert digest(cache/'chunks.json.gz')==complete['chunks_sha256']
        identity=json.loads((cache/'identity.json').read_text())
        self.profile=EmbeddingProfile(provider=BGE_M3_PROVIDER_ID,provider_kind=EmbeddingProviderKind.MODEL,model=BGE_M3_MODEL_ID,model_version=identity['model_revision'],dimension=1024,model_digest=identity['model_files']['pytorch_model.bin'],document_preprocessing=BGE_M3_PREPROCESSING,query_preprocessing=BGE_M3_PREPROCESSING)
        self.chunks=json.loads(gzip.decompress((cache/'chunks.json.gz').read_bytes()));self.vectors={};self.served=0
        texts=[build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for c in self.chunks]
        for start in range(0,len(texts),128):
            p=cache/f'vectors-{start:06d}.npy';m=json.loads(p.with_suffix('.json').read_text());batch=texts[start:start+128]
            assert digest(p)==m['vector_sha256']
            assert hashlib.sha256(json.dumps(batch,ensure_ascii=False).encode()).hexdigest()==m['input_sha256']
            vectors=np.load(p,allow_pickle=False);assert vectors.shape==(len(batch),1024) and np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors,axis=1),1,atol=1e-4)
            for text,v in zip(batch,vectors,strict=True):
                if text in self.vectors:assert np.allclose(self.vectors[text],v,atol=1e-6)
                self.vectors[text]=v.tolist()
    def embed_documents(self,texts):
        values=[self.vectors[t] for t in texts]  # mismatch fails; never infer replacement vectors
        self.served+=len(values)
        return values
    def embed_queries(self,texts):
        raise RuntimeError('Import cache does not implement online query embedding')


def main():
    root=Path('artifacts/eval/wixqa-postgres-import-2026-09-08');root.mkdir(exist_ok=False)
    cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908');provider=CachedWixVectors(cache)
    corpus=Path('/tmp/dialogpilot-rag-external-lock-20260907/wix-corpus.jsonl')
    assert digest(corpus)==json.loads((cache/'identity.json').read_text())['source_sha256']
    docs=[]
    with corpus.open() as f:
        for line in f:
            r=json.loads(line);text=r['contents'];title=r.get('title') or text.splitlines()[0][:200]
            docs.append(SourceDocument.create(source_id=str(r['id']),title=title,content=text,source_type='text',effective_from=datetime(2020,1,1,tzinfo=timezone.utc)))
    c=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
    env=dict(x.split('=',1) for x in c['Config']['Env'] if '=' in x)
    base='postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'
    name='dialogpilot_wixqa_eval_20260908'
    with psycopg.connect(base+'postgres',autocommit=True) as conn:conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    (root/'database.json').write_text(json.dumps({'host':'127.0.0.1','port':55432,'database':name,'tenant':'wixqa-eval','retained_for_followup':True,'source_sha256':digest(corpus),'script_sha256':digest(__file__)},indent=2)+'\n')
    url=base+name;PostgresMigrationRunner(url).upgrade()
    pool=PostgresPool(PostgresPoolConfig(url,min_size=1,max_size=3));pool.open()
    try:
        store=PostgresKnowledgeStore(pool,tenant_id='wixqa-eval',locale='en',chunk_strategy='fixed_tokens',embedding_provider=provider)
        started=time.monotonic()
        for start in range(0,len(docs),256):
            result=store.import_documents(docs[start:start+256])
            row={'articles_imported':min(start+256,len(docs)),'active_chunks':result.chunk_count,'cached_vectors_served':provider.served,'generation_id':result.generation_id,'elapsed_seconds':time.monotonic()-started}
            with (root/'batches.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
            print(json.dumps(row),flush=True)
        active=store.active_generation();assert store.doc_count()==len(provider.chunks)
        sources=store._active_sources();assert len(sources)==len(docs)==6221
        with pool.transaction() as conn:
            rows=conn.execute('SELECT source_id,start_char,end_char,retrieval_text FROM retrieval.knowledge_source_chunk_specs WHERE tenant_id=%s AND generation_id=%s',('wixqa-eval',active.generation_id)).fetchall()
        expected={(c['source_id'],c['start_char'],c['end_char']):build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for c in provider.chunks}
        assert len(rows)==len(expected)
        assert all(expected[(s,a,b)]==t for s,a,b,t in rows)
        report={'articles':len(sources),'chunks':store.doc_count(),'generation_id':active.generation_id,'state':active.state.value,'cached_vectors_served':provider.served,'all_source_positions_and_retrieval_text_match':True,'api_calls':0,'new_embeddings':0,'elapsed_seconds':time.monotonic()-started}
        (root/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    finally:pool.close()

if __name__=='__main__':main()
