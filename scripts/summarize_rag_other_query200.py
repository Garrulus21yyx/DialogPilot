"""Paired clustered estimates and explicit query-output caveats."""
import json
from collections import defaultdict
import numpy as np
from scripts.run_rag_other_query200 import OUT
from scripts.run_rag_fresh100 import ROOT
from scripts.replay_rag_rank_selection import read

def main():
    selection=read(ROOT/'selection.json');summary={}
    for name in ('MTRAG','WixQA'):
        rows=read(OUT/name/'cases.json.gz');groups={r['id']:r['group_id'] for r in selection[name]};d=defaultdict(list)
        for r in rows:d[groups[r['id']]].append(r['model']['recall']-r['baseline']['recall'])
        sums=np.array([sum(v) for v in d.values()]);counts=np.array([len(v) for v in d.values()]);rng=np.random.default_rng(20260908);ix=rng.integers(0,len(d),size=(20000,len(d)));delta=sums[ix].sum(axis=1)/counts[ix].sum(axis=1)
        summary[name]={**read(OUT/name/'report.json'),'groups':len(d),'paired_recall_delta':float(sums.sum()/100),'cluster_bootstrap_95':np.quantile(delta,[.025,.975]).tolist()}
    (OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
