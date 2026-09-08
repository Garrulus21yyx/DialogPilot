"""Keep dataset-specific metrics separate while recording the shared decision."""
import json,hashlib
from pathlib import Path

def main():
    paths={'doc2dial':'artifacts/eval/rag-doc2dial-balanced300-2026-09-08/report.json','mtrag':'artifacts/eval/rag-mtrag-balanced35-2026-09-08/pack/report.json','wixqa':'artifacts/eval/wixqa-fixed-heldout20-2026-09-08/report.json'}
    data={k:json.loads(Path(p).read_text()) for k,p in paths.items()}
    rows=[]
    for a,b in [('baseline','0.25'),('balanced','0.5')]:
        d=data['doc2dial']['arms'][a]
        rows.append({'dataset':'Doc2Dial','split':'consumed dev','n':300,'weight':b,'metric':'all_evidence_case_rate','recall':d['packed_complete']/300,'mrr5':d['mrr5'],'ndcg5':d['ndcg5']})
    for b in ['0.25','0.5']:
        d=data['mtrag']['summary'][b]
        rows.append({'dataset':'MTRAG','split':'previous test now consumed, .5 post-hoc','n':35,'weight':b,'metric':'official_passage_recall','recall':d['recall@5'],'mrr5':d['mrr@5'],'ndcg5':d['ndcg@5']})
        d=data['wixqa']['summary'][b]['pack5']
        rows.append({'dataset':'WixQA','split':'previous heldout now consumed','n':20,'weight':b,'metric':'article_recall','recall':d['article_recall'],'mrr5':d['article_mrr'],'ndcg5':d['article_ndcg']})
    report={'source_hashes':{k:hashlib.sha256(Path(p).read_bytes()).hexdigest() for k,p in paths.items()},'sources':paths,'rows':rows,'decision':'Adopt .5/.5 code bootstrap default; explicit deployment overrides preserved. Evidence favors this practical baseline, not SOTA or end-to-end accuracy proof.','limits':['Different qrel units and corpora; do not macro-average as a single accuracy','BGE local reranking; actual Flash answer validation is separate','Doc2Dial uses existing 100-document dev corpus; not a full Doc2Dial corpus claim','No fresh three-dataset heldout attestation','MTRAG uses official query rewrite; not real Conversation Agent rewrite'],'remaining':['same-entry limited answer acceptance across three datasets','source/metadata/parser/performance scope reporting']}
    out=Path('artifacts/eval/rag-three-dataset-decision-2026-09-08');out.mkdir(exist_ok=True)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(rows,indent=2))
if __name__=='__main__':main()
