"""Read-only full-collection MTRAG reference backend for real Agent queries.

Not a PostgreSQL/ANN benchmark. Every source and vector shard is content-bound.
"""
import asyncio,hashlib,json
from pathlib import Path
from dataclasses import dataclass
import numpy as np
from scripts.run_mtrag_dense_shards import blocks
from scripts.run_mtrag_lexical_query_pair import stream_bm25
from mcp.rank_fusion import fuse_rankings
from application.knowledge_retriever import KnowledgeCandidateResult,RetrievalStatus

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
@dataclass(frozen=True)
class MtragEmbeddingIdentity:
    fingerprint: str

@dataclass(frozen=True)
class MtragGeneration:
    manifest_hash: str
    generation_id: str
    backend_fingerprint: str
    lexical_ranker: str
    embedding_profile: MtragEmbeddingIdentity

class MtragLiveSource:
    def __init__(self,model,domain='cloud'):
        self.model=model;self.domain=domain;self.records=[]
        cache=Path('/tmp/dialogpilot-mtrag-dense-full-20260907')
        manifest_path=Path('/tmp/dialogpilot-mtrag-adapted-v2-20260907/manifest.json')
        manifest=json.loads(manifest_path.read_text());identity=json.loads((cache/'identity.json').read_text())
        assert identity['source_manifest_sha256']==sha(manifest_path)
        archive=Path('/tmp/dialogpilot-mtrag-corpora-20260907')/(domain+'.jsonl.zip')
        assert sha(archive)==manifest['source']['archives'][domain]['sha256']
        self.manifest=sha(manifest_path);self.revision=manifest['source']['revision'];self.docs={};vectors=[]
        for bi,batch in enumerate(blocks(archive,domain,4096)):
            p=cache/f'{domain}-{bi:04d}.npy';meta=json.loads(p.with_suffix('.json').read_text())
            assert meta['input_sha256']==hashlib.sha256(json.dumps(batch,ensure_ascii=False).encode()).hexdigest()
            assert meta['vectors_sha256']==sha(p)
            v=np.load(p,allow_pickle=False);assert v.shape==(len(batch),1024) and np.isfinite(v).all()
            # Preserve the fingerprinted FP16-inference vectors exactly; their
            # float32 storage does not imply float32 normalization precision.
            vectors.append(v);self.docs.update(batch)
        assert len(self.docs)==manifest['source']['stats'][domain]['passages']
        self.ids=list(self.docs);self.vectors=np.concatenate(vectors);self.hashes={p:hashlib.sha256(t.encode()).hexdigest() for p,t in self.docs.items()}
        self.generation=MtragGeneration(self.manifest,'mtrag-locked-'+domain,'MTRAG_LOCAL_EXACT_BGE_BM25','MTRAG_STREAM_BM25',MtragEmbeddingIdentity(sha(cache/'identity.json')))
    def active_generation(self):return self.generation
    def collection_scope(self,generation):
        assert generation==self.generation
        return 'en',''
    def filter_contract_snapshot(self):return {'catalog_id':'unconfigured','sales_channels':{}}
    def _valid(self,pack):
        try:
            if pack['index_manifest_fingerprint']!=self.manifest or not pack['items']:return False
            for item in pack['items']:
                r=item['source_ref'];p=r['source_id'];text=self.docs[p]
                if r['source_revision']!=self.revision or r['checksum']!=self.hashes[p] or r['scope']!='public':return False
                if not 0<=r['start_char']<r['end_char']<=len(text):return False
                if item.get('text',text[r['start_char']:r['end_char']])!=text[r['start_char']:r['end_char']]:return False
            return True
        except (KeyError,TypeError):return False
    def validate_publication_evidence(self,packs):return bool(packs) and all(self._valid(p['evidence_pack']) for p in packs)
    def validate(self,pack,request):return request.tenant_id=='mtrag-real-eval' and self._valid(pack.to_dict(include_text=True))
    def validate_candidates(self,candidates,request):return False # no candidate cache configured
    async def search_variants_async(self,request,variants,*,top_k):
        if request.tenant_id!='mtrag-real-eval' or request.manifest_fingerprint!=self.manifest or request.generation_id!=self.generation.generation_id or any((request.applicable_region,request.applicable_channel,request.applicable_product)):
            return KnowledgeCandidateResult(RetrievalStatus.INVALID_CONTRACT,detail_code='UNSUPPORTED_EVAL_SCOPE')
        return await asyncio.to_thread(self._search,request,variants,top_k)
    def _search(self,request,variants,top_k):
        assert len(variants)==1 and variants[0][1]==request.query
        q=request.query
        vec=np.asarray(self.model.encode([q],batch_size=1,max_length=8192,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs'])[0]
        dense=self.vectors@vec
        dense_ids=sorted(range(len(self.ids)),key=lambda i:(-float(dense[i]),self.ids[i]))[:20]
        ids,scores=stream_bm25([q],self.docs.items());assert ids==self.ids
        lexical=sorted((i for i,v in enumerate(scores[0]) if v>0),key=lambda i:(-scores[0,i],ids[i]))[:20]
        routes={'dense':[ids[i] for i in dense_ids],'bm25':[ids[i] for i in lexical]}
        selected=fuse_rankings(routes,weights={'dense':request.policy.dense_weight,'bm25':request.policy.lexical_weight},rrf_k=request.policy.rrf_k,top_k=top_k)
        raw=[]
        for p in selected:
            raw.append({'chunk_id':p,'source_id':p,'source_revision':self.revision,'source_checksum':self.hashes[p],'index_manifest_fingerprint':self.manifest,'scope':'public','source_start_char':0,'source_end_char':len(self.docs[p]),'content':self.docs[p],'title':self.docs[p].split('\n')[0][:200],'ranks':{r:v.index(p)+1 for r,v in routes.items() if p in v}})
        self.records.append({'query':q,'routes':routes,'fused_candidates':raw,'collection':self.domain,'collection_size':len(ids)})
        return KnowledgeCandidateResult(RetrievalStatus.OK,tuple(raw))
