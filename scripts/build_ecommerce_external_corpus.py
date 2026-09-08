"""Expand immutable synthetic evidence with attributed external background.

No QA answers from the external set are imported. Exact source IDs and hashes
separate original evidence, synthetic scope distractors, and Wix help articles.
"""
import argparse,gzip,hashlib,json
from pathlib import Path
from collections import Counter
from mcp.document_chunker import DocumentChunker


def main():
    p=argparse.ArgumentParser();p.add_argument('--wix-corpus',type=Path,default=Path('/tmp/dialogpilot-rag-external-lock-20260907/wix-corpus.jsonl'));p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.output.exists():raise ValueError('output must be new')
    root=Path('data/eval/ecommerce-complex-v2');docs=json.loads((root/'corpus.json').read_text())
    rules=json.loads((root/'rules.json').read_text())
    for i,(topic,_q,*clauses) in enumerate(rules):
        for channel in ['线下特约门店','海外直营']:
            # Same topic and overlapping clauses, but expressly different scope.
            # These are synthetic distracting policies, never target-store gold.
            text=f'# 模拟星河商城{channel} · {topic}\n\n仅适用{channel}购买，不适用中国大陆官网订单。\n\n'
            for title,clause in zip(['基础条款','限制说明','办理办法'],clauses):
                text+=f'## {title}\n{clause}\n\n'
            text+=f'## 渠道补充\n{channel}须由原销售门店受理，官网受理编号不能代替本渠道受理凭证。涉及退款的标准运费补贴上限为二十元；涉及安装的服务仅限购货地区，其他地区需另行预约。\n'
            docs.append({'source_id':f'complex:scope-negative:{i}:{channel}','title':f'{channel}{topic}规则','content':text,'metadata':{'source_type':'markdown','synthetic':True,'corpus_role':'near_scope_negative'}})
    external=[]
    for line in a.wix_corpus.read_text().splitlines():
        row=json.loads(line);body=row['contents'];external.append({'source_id':'wix:'+str(row['id']),'title':row.get('title') or body.splitlines()[0][:200],'content':body,'metadata':{'source_type':'text','origin':'WixQA','corpus_role':'external_background','source_url':row.get('url',''),'license':'MIT'}})
    assert len(external)==6221
    docs+=external;assert len({d['source_id'] for d in docs})==len(docs)
    counts=Counter();splitter=DocumentChunker()
    for d in docs:
        role=d['metadata'].get('corpus_role','target_and_initial_distractors')
        counts[role]+=len(splitter.split(d['content'],max_tokens=512,overlap_tokens=64,source_type=d['metadata']['source_type']))
    a.output.mkdir(parents=True);raw=json.dumps(docs,ensure_ascii=False).encode();(a.output/'corpus.json').write_bytes(raw);(a.output/'corpus.json.gz').write_bytes(gzip.compress(raw,mtime=0))
    manifest={'documents':len(docs),'external_documents':len(external),'synthetic_near_scope_documents':60,'chunks_structure_512_64':dict(counts),'chunks_total':sum(counts.values()),'original_corpus_sha256':hashlib.sha256((root/'corpus.json').read_bytes()).hexdigest(),'wix_raw_sha256':hashlib.sha256(a.wix_corpus.read_bytes()).hexdigest(),'expanded_corpus_sha256':hashlib.sha256(raw).hexdigest(),'external_source':'https://huggingface.co/datasets/Wix/WixQA','external_corpus_download':'https://huggingface.co/datasets/Wix/WixQA/resolve/main/wix_kb_corpus/wix_kb_corpus.jsonl','license':'Wix data card MIT; synthetic additions authored in this project','query_gold_unchanged':True,'heldout_executed':False,'api_calls':0,'evaluation_status':'CORPUS_BUILT_NOT_RETRIEVAL_SCORED','limitations':['English external background is not equivalent to hard Chinese ecommerce negatives','Synthetic near-scope policies intentionally share wording; not independently authored enterprise documents','Applicability is textual here; not a metadata lifecycle test']}
    (a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
