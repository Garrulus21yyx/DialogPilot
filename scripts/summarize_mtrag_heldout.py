"""Fixed paired metrics and conversation-level bootstrap; no parameter selection."""
import gzip,json
from pathlib import Path
import numpy as np

ROOT=Path('artifacts/eval/rag-g4-mtrag-heldout35-2026-09-08')

def main():
    retrieval=json.loads(gzip.decompress((ROOT/'retrieval/cases.json.gz').read_bytes()))
    pack=json.loads(gzip.decompress((ROOT/'pack/cases.json.gz').read_bytes()))
    summary={a:{'candidate_recall@20':sum(r['fusion'][a]['metrics']['recall@20'] for r in retrieval)/len(retrieval),**{k:sum(r['arms'][a]['metrics'][k] for r in pack)/len(pack) for k in pack[0]['arms'][a]['metrics']}} for a in ('0.25','0.75')}
    rng=np.random.default_rng(20260908);samples=rng.integers(0,len(pack),size=(10000,len(pack)))
    differences={}
    for metric in pack[0]['arms']['0.25']['metrics']:
        delta=np.array([r['arms']['0.75']['metrics'][metric]-r['arms']['0.25']['metrics'][metric] for r in pack])
        differences[metric]={'delta':float(delta.mean()),'bootstrap_percentile95':np.quantile(delta[samples].mean(axis=1),[.025,.975]).tolist(),'better':int((delta>1e-12).sum()),'worse':int((delta< -1e-12).sum())}
    report={'scope':'35 heldout conversations, one hash-selected official-rewrite question each; frozen two-arm retrieval/CE/pack, no answers','summary':summary,'paired_pack':differences,'bootstrap':{'seed':20260908,'resamples':10000,'unit':'conversation, one query/group','stratified':False},'api_calls':0,'query_embeddings':35,'document_embeddings':0,'new_reranker_pairs':1212,'heldout_now_consumed':True}
    (ROOT/'summary.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
