"""Additional public queries not used to select this SQL repair; no model calls."""
import gzip,json,random
from pathlib import Path
from scripts import benchmark_bm25_postings_pair as pair
if __name__=='__main__':
 rows=[json.loads(line)['case'] for line in gzip.open('artifacts/eval/wixqa-pg-compact-heldout20-2026-09-08/cases.jsonl.gz','rt')]
 selected=random.Random(20260909).sample(rows,6)
 pair.ROOT=Path('artifacts/eval/bm25-postings-additional6-final-2026-09-09')
 pair.main(queries=[(r['group_id'],r['query']) for r in selected])
