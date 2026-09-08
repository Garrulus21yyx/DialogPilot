"""Frozen parent-local candidate pool -> local CE -> existing pack/serialization."""
import gzip,json
from pathlib import Path
from types import SimpleNamespace
from scripts.run_mtrag_reranker_pair import run as rerank
from scripts.replay_mtrag_packing import run as pack
from scripts.run_mtrag_lexical_query_pair import metrics
from scripts.adapt_mtrag_retrieval_dataset import digest

def main():
    b=Path('artifacts/eval');source=b/'rag-g4-cloud-parent-child8-2026-09-08/report.json'
    original=json.loads(source.read_text());root=b/'rag-g4-cloud-parent-validation8-2026-09-08';root.mkdir(exist_ok=False)
    hybrid=root/'hybrid';hybrid.mkdir();rows=[]
    for r in original['cases']:
        rows.append({'case_id':r['case_id'],'group_id':'mtrag-'+r['case_id'].split('<::>')[0],'domain':'cloud','query':r['query'],'gold':r['gold'],'variants':{a:{'ranking':r[k],'metrics':metrics(r[k],set(r['gold']))} for a,k in [('dense','baseline'),('parent_local','candidate')]}})
    (hybrid/'cases.json.gz').write_bytes(gzip.compress(json.dumps(rows).encode(),mtime=0))
    (hybrid/'identity.json').write_text(json.dumps({'candidate_report_sha256':digest(source),'arms':{'dense':'original Dense top20','parent_local':'parent BM25 top3, each local BM25 top2; then Dense fill20'}})+'\n')
    args=SimpleNamespace(hybrid=hybrid,manifest=b/'rag-g4-mtrag-adapter-2026-09-07/manifest.json',corpora=Path('/tmp/dialogpilot-mtrag-corpora-20260907'),model=Path('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3'),output=root/'rerank',replay=None,arms=['dense','parent_local'])
    rerank(args)
    args.rerank=args.output;args.output=root/'pack';pack(args)
if __name__=='__main__':main()
