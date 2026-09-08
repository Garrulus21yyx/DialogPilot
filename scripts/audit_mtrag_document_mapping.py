"""Verify official parent identifiers by exact source-offset containment, never prefix alone."""
import gzip,json,zipfile,re
from pathlib import Path
from collections import Counter
from scripts.adapt_mtrag_retrieval_dataset import digest,DOMAINS
BASE=Path('artifacts/eval')
def readzip(p,d):
    with zipfile.ZipFile(p) as z,z.open(d+'.jsonl') as f:
        for line in f:yield json.loads(line)
def main():
    stats={};mapping={};hashes={};examples={}
    manifest=json.loads((BASE/'rag-g4-mtrag-adapter-2026-09-07/manifest.json').read_text())
    for domain in DOMAINS:
        dp=Path('/tmp/dialogpilot-mtrag-documents-20260908')/f'{domain}.jsonl.zip'
        pp=Path('/tmp/dialogpilot-mtrag-corpora-20260907')/f'{domain}.jsonl.zip'
        assert digest(pp)==manifest['source']['archives'][domain]['sha256']
        docs={};count=Counter();bad=[]
        for r in readzip(dp,domain):
            key=r.get('document_id',r.get('_id'))
            assert key and key not in docs
            r['_normalized_text']=re.sub(r' {2,}', ' ', r['text'])
            docs[key]=r
        for r in readzip(pp,domain):
            if not r['text'].strip():count['empty_excluded']+=1;continue
            pid=r['_id'];parts=pid.rsplit('-',2)
            state='invalid_offset_id';parent=None
            if len(parts)==3 and parts[1].isdigit() and parts[2].isdigit():
                parent=parts[0];start,end=map(int,parts[1:])
                if parent not in docs:state='parent_missing'
                elif not 0<=start<=end<=len(docs[parent]['text']):state='offset_out_of_bounds'
                else:
                    doc=docs[parent];text=doc['text'];body=text[start:end]
                    title=doc.get('title') or ''
                    if body==r['text']:state='verified_exact'
                    elif title+'\n'+body==r['text']:state='verified_title_prefix'
                    elif title+'\n'+doc['_normalized_text'][start:end]==r['text']:state='verified_space_normalized_title_prefix'
                    else:state='text_mismatch'
                    if state.startswith('verified'):mapping[f'mtrag:{domain}:{pid}']=f'mtrag:{domain}:{parent}'
            count[state]+=1
            if not state.startswith('verified') and len(bad)<5:bad.append({'id':pid,'state':state})
        stats[domain]={'official_documents':len(docs),**count};examples[domain]=bad
        hashes[domain]={'documents':digest(dp),'passages':digest(pp)}
    out=BASE/'rag-g4-document-mapping-2026-09-08';out.mkdir(exist_ok=False)
    with gzip.open(out/'mapping.json.gz','wt') as f:json.dump(mapping,f)
    (out/'report.json').write_text(json.dumps({'revision':'2c618bb98db3c8526433e22d8a2f7320f10a7470','contract':'Official ID exists; exact slice or title+newline+slice, separately classified for ASCII-space-normalized source; equality is exact' ,'api_calls':0,'stats':stats,'failure_examples':examples,'hashes':hashes},indent=2)+'\n')
    print(json.dumps(stats,indent=2));print(json.dumps(examples,indent=2))
if __name__=='__main__':main()
