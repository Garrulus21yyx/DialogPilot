import gzip,hashlib,json
from pathlib import Path


def test_review_binds_all_frozen_answers_and_counts_separate_uncertainty():
    root=Path('artifacts/eval')
    rows=[json.loads(l) for l in gzip.decompress((root/'rag-g4-mtrag-answer8-2026-09-08/answers.jsonl.gz').read_bytes()).splitlines()]
    review=json.loads((root/'rag-g4-mtrag-answer8-review-2026-09-08/review.json').read_text())
    assert len(review['records'])==len(rows)==16
    for r,v in zip(rows,review['records'],strict=True):
        assert (r['case_id'],r['arm'])==(v['case_id'],v['arm'])
        assert hashlib.sha256(r['answer'].encode()).hexdigest()==v['answer_sha256']
    for a,counts in review['summary'].items():
        assert sum(counts.values())==8
        assert counts=={k:sum(r['arm']==a and r['support']==k for r in review['records']) for k in counts}
