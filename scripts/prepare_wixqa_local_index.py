"""Full-corpus WixQA chunks and resumable local production-provider vectors."""
import argparse,gzip,json,hashlib,os,fcntl
from pathlib import Path
from importlib.metadata import version
import numpy as np
from mcp.document_chunker import DocumentChunker
from application.knowledge_retrieval_text import build_child_retrieval_text
from infrastructure.bge_m3_embedding import LocalBGEM3EmbeddingProvider,BGEM3EmbeddingConfig


def digest(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--encode',action='store_true');args=parser.parse_args()
    root=Path('/tmp/dialogpilot-wixqa-full-index-20260908');root.mkdir(exist_ok=True)
    with (root/'lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        src=Path('/tmp/dialogpilot-rag-external-lock-20260907/wix-corpus.jsonl')
        source_lock=json.loads(Path('artifacts/eval/rag-three-dataset-lock-v3-2026-09-07/wixqa-test-lock.json').read_text())
        assert digest(src)==source_lock['source_files']['wix-corpus.jsonl']['sha256']
        model=Path('/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181')
        identity={'source_sha256':digest(src),'max_tokens':512,'overlap_tokens':64,'strategy':'fixed_tokens','source_type':'text',
            'model_revision':model.name,'model_files':{p.name:digest(p) for p in model.iterdir() if p.is_file() and p.suffix in ('.bin','.json','.model')},
            'code_sha256':{p:digest(p) for p in ['mcp/document_chunker.py','core/token_estimator.py','application/knowledge_retrieval_text.py','infrastructure/bge_m3_embedding.py']},
            'libraries':{n:version(n) for n in ('sentence-transformers','torch','transformers','numpy')},'batch_size':16,'shard_size':128}
        ip=root/'identity.json'
        if ip.exists():assert json.loads(ip.read_text())==identity,'cache identity mismatch'
        else:ip.write_text(json.dumps(identity,indent=2)+'\n')
        cp=root/'chunks.json.gz'
        if cp.exists():
            chunks=json.loads(gzip.decompress(cp.read_bytes()))
        else:
            chunks=[];seen=set();splitter=DocumentChunker()
            for line in src.read_text().splitlines():
                row=json.loads(line);doc=str(row['id']);assert doc not in seen;seen.add(doc)
                text=row['contents'];title=row.get('title') or (text.splitlines()[0][:200] if text else doc)
                for c in splitter.split(text,max_tokens=512,overlap_tokens=64,strategy='fixed_tokens',source_type='text'):
                    assert text[c.start_char:c.end_char]==c.content
                    chunks.append({'id':f'{doc}:{c.start_char}:{c.end_char}','source_id':doc,'start_char':c.start_char,'end_char':c.end_char,'text':c.content,'title':title,'source_checksum':hashlib.sha256(text.encode()).hexdigest()})
            assert len(seen)==6221
            tmp=cp.with_suffix('.tmp');tmp.write_bytes(gzip.compress(json.dumps(chunks,ensure_ascii=False).encode(),mtime=0));os.replace(tmp,cp)
        report={'articles':6221,'articles_with_chunks':len({c['source_id'] for c in chunks}),'chunks':len(chunks),'api_calls':0,'chunks_sha256':digest(cp),'identity_sha256':digest(ip),'cache':str(root)}
        artifact=Path('artifacts/eval/wixqa-local-index-2026-09-08');artifact.mkdir(exist_ok=True)
        (artifact/'preparation.json').write_text(json.dumps(report,indent=2)+'\n');(artifact/'identity.json').write_bytes(ip.read_bytes())
        print(json.dumps(report),flush=True)
        if not args.encode:return
        provider=LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(model,model.name,identity['model_files']['pytorch_model.bin'],device='cuda',batch_size=16))
        for start in range(0,len(chunks),128):
            batch=chunks[start:start+128];texts=[build_child_retrieval_text(title=c['title'],section_path=(),content=c['text']) for c in batch]
            ih=hashlib.sha256(json.dumps(texts,ensure_ascii=False).encode()).hexdigest();vp=root/f'vectors-{start:06d}.npy';mp=vp.with_suffix('.json')
            if vp.exists() and mp.exists():
                meta=json.loads(mp.read_text());assert meta['input_sha256']==ih and meta['vector_sha256']==digest(vp)
                continue
            lengths=[len(x) for x in provider._model.tokenizer(texts,truncation=False)['input_ids']]
            assert max(lengths)<=provider._model.max_seq_length,'embedding would truncate input'
            vectors=np.asarray(provider.embed_documents(texts),dtype=np.float32)
            assert vectors.shape==(len(batch),1024) and np.isfinite(vectors).all()
            tmp=vp.with_suffix('.tmp')
            with tmp.open('wb') as f:np.save(f,vectors)
            os.replace(tmp,vp)
            meta={'input_sha256':ih,'vector_sha256':digest(vp),'rows':len(batch),'max_tokens':max(lengths)}
            tmp=mp.with_suffix('.tmp');tmp.write_text(json.dumps(meta)+'\n');os.replace(tmp,mp)
            print('encoded',start+len(batch),'/',len(chunks),flush=True)
        (root/'COMPLETE.json').write_text(json.dumps(report)+'\n')

if __name__=='__main__':main()
