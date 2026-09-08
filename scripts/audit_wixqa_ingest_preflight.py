"""Zero-model source/chunk preflight; no DB mutation or embedding claims."""
import gzip,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from application.knowledge_source import SourceRevision
from mcp.source_document import SourceDocument
from core.cost_budget import OfflineIngestBudgetExceeded


def main():
 cache=Path('/tmp/dialogpilot-wixqa-full-index-20260908')
 chunks=json.loads(gzip.decompress((cache/'chunks.json.gz').read_bytes()))
 expected={(c['source_id'],c['start_char'],c['end_char']):c for c in chunks}
 corpus=Path('/tmp/dialogpilot-rag-external-lock-20260907/wix-corpus.jsonl')
 identity=json.loads((cache/'identity.json').read_text());assert hashlib.sha256(corpus.read_bytes()).hexdigest()==identity['source_sha256']
 documents=[]
 with corpus.open() as f:
  for line in f:
   r=json.loads(line);text=r['contents'];title=r.get('title') or text.splitlines()[0][:200]
   documents.append(SourceDocument.create(source_id=str(r['id']),title=title,content=text,source_type='text'))
 store=PostgresKnowledgeStore(None,tenant_id='preflight',locale='en',chunk_strategy='fixed_tokens')
 sizes=[];seen=0
 for start in range(0,len(documents),256):
  batch=documents[start:start+256];store._validate_budget(batch)
  revisions=[SourceRevision.create(tenant_id='preflight',source_id=d.source_id,title=d.title,content=d.content,source_type='text',effective_from=datetime(2020,1,1,tzinfo=timezone.utc)) for d in batch]
  projected=store._chunks(revisions);store._validate_chunk_budget(projected);sizes.append(len(projected))
  from application.knowledge_retrieval_text import build_child_retrieval_text
  for c in projected:
   old=expected[(c.source_id,c.start_char,c.end_char)]
   assert c.retrieval_text==build_child_retrieval_text(title=old['title'],section_path=(),content=old['text'])
   seen+=1
 assert seen==len(expected)
 try:store._validate_chunk_budget(chunks)
 except OfflineIngestBudgetExceeded as e:failure={'code':e.code,'dimension':e.dimension,'observed':e.observed,'limit':e.limit}
 else:failure=None
 report={'articles':len(documents),'chunks':seen,'batch_chunks':sizes,'all_incoming_batches_pass':True,'same_production_retrieval_text':True,'cumulative_generation_check':failure,'api_calls':0,'model_calls':0,'scope':'Production chunk and budget methods, no database import; default hash provider only contributes an unused structural generation ID, no vectors created.'}
 p=Path('artifacts/eval/wixqa-ingest-preflight-2026-09-08');p.mkdir(exist_ok=True);(p/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':main()
