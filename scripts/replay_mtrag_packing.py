"""Frozen CE rankings through production packing and knowledge serialization only."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import zipfile
from mcp.context_packer import ContextCandidate, ContextPacker
from mcp.evidence_pack import EvidencePack
from mcp.tool_manager import MCPToolManager, ToolResult
from core.token_estimator import TokenEstimator
from scripts.adapt_mtrag_retrieval_dataset import DOMAINS, digest
from scripts.run_mtrag_reranker_pair import ARMS, at5


def run(a):
    arms_to_score=tuple(a.arms) if getattr(a,"arms",None) else ARMS
    ranks=json.loads(gzip.decompress((a.rerank/'cases.json.gz').read_bytes()))
    hybrid=json.loads(gzip.decompress((a.hybrid/'cases.json.gz').read_bytes()))
    identity=json.loads((a.rerank/'identity.json').read_text())
    assert identity['hybrid_sha256']==digest(a.hybrid/'cases.json.gz')
    assert identity['source_manifest_sha256']==digest(a.manifest)
    manifest=json.loads(a.manifest.read_text())
    wanted={p for r in ranks for arm in arms_to_score for p in r['arms'][arm]['ranking']}
    sources={}
    for domain in DOMAINS:
        archive=a.corpora/f'{domain}.jsonl.zip'
        assert digest(archive)==manifest['source']['archives'][domain]['sha256']
        with zipfile.ZipFile(archive) as z,z.open(domain+'.jsonl') as f:
            for line in f:
                r=json.loads(line);pid=f"mtrag:{domain}:{r['_id']}"
                if pid in wanted:
                    assert pid not in sources
                    sources[pid]=r
    assert set(sources)==wanted
    rows=[];tokens=TokenEstimator()
    for r,h in zip(ranks,hybrid,strict=True):
        assert r['case_id']==h['case_id'] and r['gold']==h['gold']
        arms={}
        for arm in arms_to_score:
            candidates=[]
            for p in r['arms'][arm]['ranking']:
                source=sources[p];text=source['text']
                candidates.append(ContextCandidate(chunk_id=p,document_id=p,text=text,start_char=0,end_char=len(text),title=source.get('title') or '',source_type='mtrag_official_passage',source_revision=manifest['source'].get('revision','locked'),source_checksum=hashlib.sha256(text.encode()).hexdigest(),index_manifest_fingerprint=digest(a.manifest)))
            packed=ContextPacker().pack(candidates,max_tokens=2600,max_chunks=5)
            pack=EvidencePack.from_packed(h['query'],packed,retrieval_policy={'max_tokens':2600,'final_k':5})
            data={'status':'OK','evidence_pack':pack.to_dict(include_text=True)} if packed.selected else {'status':'NO_EVIDENCE','evidence_pack':None}
            wire=MCPToolManager._render_for_model(None,ToolResult(success=True,data=data,tool_name='knowledge_search',authority='knowledge.active_source'))
            visible=json.loads(wire)['evidence']
            ids=[e['source']['source_id'] for e in visible]
            assert ids==list(packed.chunk_ids)
            for e in visible:
                assert e['text']==sources[e['source']['source_id']]['text']
            arms[arm]={'packed_ids':list(packed.chunk_ids),'serialized_ids':ids,'metrics':at5(ids,set(r['gold'])),'body_estimated_tokens':packed.token_count,'serialized_estimated_tokens':tokens.estimate(wire),'skipped_budget':list(packed.skipped_budget),'skipped_redundant':list(packed.skipped_redundant),'wire':wire}
        rows.append({'case_id':r['case_id'],'gold':r['gold'],'arms':arms})
    summary={arm:{m:sum(r['arms'][arm]['metrics'][m] for r in rows)/len(rows) for m in rows[0]['arms'][arm]['metrics']} for arm in arms_to_score}
    report={'scope':'production pack and serialization only; excludes guard, agent context and answer','api_calls':0,'new_model_scores':0,'case_count':len(rows),'summary':summary,'budget_skips':{a:sum(len(r['arms'][a]['skipped_budget']) for r in rows) for a in arms_to_score},'serialized_token_max':max(r['arms'][a]['serialized_estimated_tokens'] for r in rows for a in arms_to_score),'paired_recall_vs_current':{a:{'better':sum(r['arms'][a]['metrics']['recall@5']>r['arms'][arms_to_score[0]]['metrics']['recall@5'] for r in rows),'worse':sum(r['arms'][a]['metrics']['recall@5']<r['arms'][arms_to_score[0]]['metrics']['recall@5'] for r in rows)} for a in arms_to_score[1:]}}
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'cases.json.gz').write_bytes(gzip.compress(json.dumps(rows,ensure_ascii=False).encode(),mtime=0))
    (a.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (a.output/'identity.json').write_text(json.dumps({'rerank_sha256':digest(a.rerank/'cases.json.gz'),'source_manifest_sha256':digest(a.manifest),'source_files':{p:digest(Path(p)) for p in ['mcp/context_packer.py','mcp/evidence_pack.py','mcp/tool_manager.py','application/knowledge_tool_contract.py','core/token_estimator.py']}},indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('rerank','hybrid','manifest','corpora','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--arms',nargs='+',metavar='ARM')
    run(p.parse_args())
