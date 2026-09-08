"""Development-only decision-contract candidate; no production prompt mutation."""
import asyncio,gzip,hashlib,json
from pathlib import Path
from tempfile import TemporaryDirectory
from evaluation.rag_pipeline.dataset import RagDataset
import scripts.run_rag_query_calibration12 as base
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider

CONTRACT = (
    "First resolve the user's ongoing information need from the current reply and history. "
    "An answer to an earlier clarification supplies a condition for that information need; "
    "it is not merely a social acknowledgement. For a policy, procedure, requirement or product-documentation "
    "question, retrieve evidence with a knowledge goal when the supplied verified evidence does not "
    "already establish the answer. You may retrieve general rules before asking for details needed only "
    "to decide personal applicability. Use the facts already supplied rather than asking for them again. "
    "The respond branch serves greetings, acknowledgements without an outstanding information need, "
    "and clarification when the requested subject itself cannot be identified; it does not replace "
    "retrieving missing policy evidence with remembered rules or an offer to look them up later. "
)
class Candidate(AnthropicConversationPlanningProvider):
    async def _complete(self,payload,role,system,**kwargs):
        return await super()._complete(payload,role,CONTRACT+system,**kwargs)

def main():
    original=Path('artifacts/eval/rag-query-calibration12-2026-09-08')
    manifest=json.loads((original/'manifest.json').read_text())
    snapshot=json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    with TemporaryDirectory() as temp:
        for name,text in snapshot.items():Path(temp,name).write_text(text)
        ds=RagDataset.load(Path(temp),verify_checksum=True)
    lookup={c.case_id:c for c in ds.select_cases('dev')};cases=[lookup[c['case_id']] for c in manifest['cases']]
    base.OUT=Path('artifacts/eval/rag-query-contract-candidate12-2026-09-08');base.OUT.mkdir(exist_ok=False)
    manifest.update(candidate_contract=CONTRACT,candidate_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    scope='Development-only prompt decision contract candidate; same 12 cases/model/budget. No production adoption from this batch alone; keep non-knowledge conversation regression before any adoption.')
    (base.OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    base.AnthropicConversationPlanningProvider=Candidate
    rows=asyncio.run(base.plan(cases));base.evaluate(ds,cases,rows)
    for name in ('queries','retrieval'):(base.OUT/(name+'.jsonl.gz')).write_bytes(gzip.compress((base.OUT/(name+'.jsonl')).read_bytes(),mtime=0))
if __name__=='__main__':main()
