"""Frozen assistant-authored development queries; zero provider inference calls."""
import gzip
import hashlib
import json
from pathlib import Path

from evaluation.rag_pipeline.dataset import RagDataset
from scripts import run_rag_resolved_query_pair as replay


QUERIES = [
    "What does ED do specifically for borrowers whose borrower defense to repayment claims succeed based on ED's CCI findings?",
    "What should I do if my New York vehicle inspection sticker is damaged and cannot simply be reattached to the windshield?",
    "How long will my current restriction remain in effect?",
    "What vehicle inspection steps must I take after returning my vehicle to New York State?",
    "What is the fine in New York State if my vehicle inspection sticker expired within the last 60 days?",
    "How secure are New York I-PIRP course websites, and how is participants' personal information protected?",
    "Why are borrower defense applicants receiving notifications about the Sweet v. DeVos lawsuit and its proposed settlement?",
    "What happens to loan forbearance, stopped collections, and interest after ED denies or partially approves a borrower defense to repayment application?",
    "How do I request a New York vehicle inspection extension if I will be out of state when the inspection expires?",
    "When I am ready to add my daughter as a dependent to my VA disability benefits, can I file the application by mail? I currently do not have a combined disability rating of at least 30%.",
    "When do New York State vehicle safety and emissions inspections expire?",
    "When should I apply for Social Security retirement benefits?",
    "How will I find out the remaining balance of my federal student loan after the interest adjustment, in the context of the COVID-19 administrative forbearance and temporary 0% interest?",
    "En los contratos de préstamos federales para estudiantes, ¿qué es un pagaré y qué significa firmarlo?",
    "How can I obtain my Social Security Benefit Verification Letter online?",
    "What does CHAMPVA cover, and what eligibility conditions apply? I am not the spouse, surviving spouse, or child of a qualifying Veteran and do not qualify for TRICARE; the discussion also mentioned primary family caregivers.",
    "What Social Security survivor benefits may be available to my spouse and children after my death if Social Security is my income?",
    "What should I consider before accepting a private student loan as part of choosing financial aid from my school?",
    "What happens if our business rejects a deal offered after the New York State DMV investigates a complaint against us?",
    "Which VA-related health coverage programs qualify as ACA minimum essential coverage? I am asking in the context of 2019, when I did not have health insurance.",
]


def main():
    root = Path('artifacts/eval')
    out = root/'rag-authored-query20-2026-09-07'
    out.mkdir(exist_ok=False)
    ds = RagDataset.load(root/'doc2dial-rag-mini-dev-v1', verify_checksum=True)
    lookup = {c.case_id:c for c in ds.select_cases('dev')}
    selection = json.loads((root/'rag-composition20-selected-2026-09-07/manifest.json').read_text())['case_ids']
    assert len(selection)==len(QUERIES)==20
    cases = [lookup[cid] for cid in selection]
    rows = [{'case_id':c.case_id,'raw_query':c.query,'resolved_queries':[q], 'calls':[], 'author':'assistant', 'input_sufficiency':'insufficient_restriction_type' if i==2 else 'contextual_reference_query', 'history':list(c.history)} for i,(c,q) in enumerate(zip(cases,QUERIES))]
    frozen=''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows)
    (out/'queries.jsonl').write_text(frozen)
    (out/'manifest.json').write_text(json.dumps({'scope':'assistant-authored development query diagnostic; previously exposed cases, not blind/oracle gold', 'api_calls':0,'frozen_queries_sha256':hashlib.sha256(frozen.encode()).hexdigest(),'dataset':ds.manifest,'case_count':20,'unresolvable_case_ids':[cases[2].case_id],'selection':'same preselected conversation hashes; no outcome reselection','query_changes_after_scoring':0},indent=2)+'\n')
    print('QUERIES_FROZEN',hashlib.sha256(frozen.encode()).hexdigest(),flush=True)
    replay.OUT=out
    replay.evaluate(ds,cases,rows)
    report=json.loads((out/'report.json').read_text())
    report['scope']='assistant-authored frozen queries vs raw; local retrieval/CE only; no online rewrite or generation claim'
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    results=[json.loads(l) for l in (out/'retrieval.jsonl').read_text().splitlines()]
    arms={a:{r['case_id']:r for r in results if r['arm']==a} for a in ('raw','resolved')}
    paired={m:{'rescues':sum(not arms['raw'][k][m] and arms['resolved'][k][m] for k in selection),'harms':sum(arms['raw'][k][m] and not arms['resolved'][k][m] for k in selection)} for m in ('complete20','complete5')}
    (out/'paired.json').write_text(json.dumps(paired,indent=2)+'\n')
    for name in ('queries','retrieval'):
        (out/(name+'.jsonl.gz')).write_bytes(gzip.compress((out/(name+'.jsonl')).read_bytes(),mtime=0))
    print(json.dumps(paired),flush=True)


if __name__=='__main__':main()
