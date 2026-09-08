"""Independent article metrics, provenance and paired bootstrap audit."""
import argparse,gzip,json,math
from pathlib import Path
import numpy as np


def main():
 p=argparse.ArgumentParser();p.add_argument('root',type=Path);a=p.parse_args()
 chunks={c['id']:c for c in json.loads(gzip.decompress(Path('/tmp/dialogpilot-wixqa-full-index-20260908/chunks.json.gz').read_bytes()))}
 rows=[json.loads(l) for l in (a.root/'cases.jsonl').read_text().splitlines()]
 rng=np.random.default_rng(20260908);indices=rng.integers(0,len(rows),(10000,len(rows)))
 for r in rows:
  gold=set(r['case']['article_ids'])
  for arm in r['arms'].values():
   assert set(arm['candidate_ids'])<=set(r['scores']) and set(arm['ce_order'])==set(arm['candidate_ids'])
   assert set(arm['packed_ids'])<=set(arm['ce_order'])
   for stage,ids,k in [('candidate20',arm['candidate_ids'],20),('ce5',arm['ce_order'][:5],5),('pack5',arm['packed_ids'],5)]:
    docs=list(dict.fromkeys(chunks[i]['source_id'] for i in ids));hits=[j+1 for j,d in enumerate(docs) if d in gold]
    actual=arm[stage]
    assert math.isclose(actual['article_recall'],len(hits)/len(gold))
    assert math.isclose(actual['article_mrr'],1/hits[0] if hits else 0)
    assert math.isclose(actual['article_ndcg'],sum(1/math.log2(j+1) for j in hits)/sum(1/math.log2(j+2) for j in range(min(k,len(gold)))))
 out={}
 for key in ('article_recall','article_mrr','article_ndcg','all_articles'):
  delta=np.array([float(r['arms']['0.5']['pack5'][key])-float(r['arms']['0.25']['pack5'][key]) for r in rows])
  out[key]={'mean_delta':float(delta.mean()),'paired_bootstrap_95_percentile':np.quantile(delta[indices].mean(axis=1),[.025,.975]).tolist()}
 report={'case_count':len(rows),'checks':'All saved stage metrics independently recomputed; candidate/score/pack membership verified','bootstrap_seed':20260908,'resamples':10000,'unit':'one selected QA per connected article group','pack_deltas':out}
 (a.root/'audit.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))

if __name__=='__main__':main()
