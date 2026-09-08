"""Freeze new cases before inference; conservative local exposure exclusion."""
import json,gzip,hashlib
from pathlib import Path
from collections import defaultdict,Counter
from scripts.freeze_wixqa_comparison import components
from evaluation.doc2dial_heldout_dataset import _load_member,_maximum_history_case,document_length_bucket
from evaluation.rag_pipeline.dataset import write_dataset
ROOT=Path('artifacts/eval/rag-fresh100-2026-09-08')
CACHE=Path('/tmp/dialogpilot-rag-external-lock-20260907')
def key(v):return hashlib.sha256(('rag-fresh100-v1\0'+str(v)).encode()).hexdigest()
def roundrobin(groups,n):
    queues=[sorted(v,key=lambda r:key(r['id'])) for _,v in sorted(groups.items(),key=lambda x:key(x[0]))];chosen=[]
    while len(chosen)<n and any(queues):
        for q in queues:
            if q and len(chosen)<n:chosen.append(q.pop(0))
    assert len(chosen)==n,(len(chosen),n)
    return chosen

def main():
    ROOT.mkdir(exist_ok=False)
    invpath=Path('artifacts/eval/rag-fresh100-inventory-2026-09-08/inventory.json.gz');inv=json.loads(gzip.decompress(invpath.read_bytes()));assert not inv['parse_errors']
    docs=_load_member(Path('/tmp/doc2dial_v1.0.1.zip'),'doc2dial_doc.json')['doc_data'];dials=_load_member(Path('/tmp/doc2dial_v1.0.1.zip'),'doc2dial_dial_test.json')['dial_data'];allowed=set(inv['doc2dial']['not_observed_ids']);groups=defaultdict(list);corpus=[]
    for domain,dd in docs.items():
        for did,d in dd.items():corpus.append({'id':did,'title':d.get('title') or did,'content':d['doc_text'],'metadata':{'domain':domain,'source_type':'text'}})
        for did,convs in dials[domain].items():
            for conv in convs:
                if 'doc2dial-'+conv['dial_id'] not in allowed:continue
                c=_maximum_history_case(domain=domain,bucket=document_length_bucket(len(dd[did]['doc_text'])),document_id=did,document=dd[did],dialogue=conv)
                if c:groups[domain].append(c)
    dc=roundrobin(groups,100);write_dataset(ROOT/'doc2dial',dataset_id='doc2dial-fresh100-v1',documents=corpus,cases=dc,source={'selection':'one maximum-history answerable turn per locally unobserved official test conversation; domain roundrobin hash order','inventory_sha256':hashlib.sha256(invpath.read_bytes()).hexdigest()})
    mt_allowed={g['conversation_id'] for g in inv['mtrag']['groups'] if not g['identity_match'] and not g['query_match']};variants={r['task_id']:r for r in map(json.loads,open('/tmp/dialogpilot-mtrag-adapted-v2-20260907/official-queries.jsonl'))};groups=defaultdict(list)
    for r in map(json.loads,open('/tmp/dialogpilot-mtrag-adapted-v2-20260907/cases.jsonl')):
        if r['group_id'].removeprefix('mtrag-') in mt_allowed:
            r['query']=variants[r['id']]['queries']['rewrite'];groups[r['group_id']].append(r)
    mt=roundrobin(groups,100)
    wr=[]
    for config in ('wix-expertwritten','wix-simulated'):
        flags={r['row_index']:r for r in inv['wixqa'][config]['matches']}
        for i,r in enumerate(map(json.loads,open(CACHE/(config+'.jsonl')))):
            wr.append({'id':config+':'+str(i),'config':config,'row_index':i,'query':r['question'],'article_ids':r['article_ids'],'exposed':flags[i]['query_match'] or flags[i]['article_match']})
    # Connected groups propagate both old article and old question exposure.
    old=json.loads(Path('artifacts/eval/wixqa-connected-comparison-2026-09-08/manifest.json').read_text());oldids={r['config']+':'+str(r['row_index']) for arm in old['selected'].values() for r in arm}
    groups=defaultdict(list)
    for cc in components([r['article_ids'] for r in wr]):
        if any(wr[i]['exposed'] or wr[i]['id'] in oldids for i in cc):continue
        gid=key('|'.join(sorted({a for i in cc for a in wr[i]['article_ids']})))
        selected=min((wr[i] for i in cc),key=lambda r:key(r['id']));selected['group_id']=gid;groups[selected['config']].append(selected)
    wx=roundrobin(groups,100)
    result={'scope':'Not present in declared local inventory; not a global unseen attestation. Same-group MTRAG turns stay together in uncertainty estimates. No inference before freeze.',
        'seed':'rag-fresh100-v1','inventory_sha256':hashlib.sha256(invpath.read_bytes()).hexdigest(),'api_budget':0,'new_ce_pair_cap':14000,
        'strategies':{'baseline':'Dense/BM25 .5 each; 20 candidate; CE; 5/2600','Doc2Dial':'route20 union<=40; CE .75 / original rank .25 k10','MTRAG':'candidate20; CE .75 / original rank .25 k10','WixQA':'route20 union<=40; CE only'},
        'counts':{'Doc2Dial':{'n':len(dc),'groups':len({r['group_id'] for r in dc}),'documents':len(corpus)},'MTRAG':{'n':len(mt),'groups':len({r['group_id'] for r in mt})},'WixQA':{'n':len(wx),'groups':len({r['group_id'] for r in wx})}},'MTRAG':mt,'WixQA':wx}
    (ROOT/'selection.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(result['counts'])
if __name__=='__main__':main()
