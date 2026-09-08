"""Freeze source-backed nonblind diagnostic observations; never alter official qrels."""
import gzip,json,hashlib,zipfile
from pathlib import Path
from scripts.adapt_mtrag_retrieval_dataset import digest
B=Path('artifacts/eval')
LANG='e1b602e47ded79a35d8df4eefe194e39<::>1'
WEB='fdee20f7fd677e420742b09989623d68<::>6'
OBS=[
(LANG,'ibmcld_03329-1102-2607','If the dialog you plan to build will use a language other than English, then choose the appropriate language from the list.','DIRECT_SUPPORT','Directly describes creating a dialog skill in a non-English language; not in this query official qrels.'),
(LANG,'ibmcld_03120-3469-5331','From the Language field, choose Another language.','DIRECT_SUPPORT','Explains dialog/actions skill universal language setup, with instruction to prefer a listed built-in language when available.'),
(LANG,'ibmcld_03369-64600-66787','The Dialog skill analysis notebook was updated with language support','RELATED_NOT_SUFFICIENT_ALONE','Gold speaks about analysis notebook languages, a different capability from creation of a dialog skill.'),
(WEB,'ibmcld_16365-7-1700','By default, the web chat launcher appears in a small initial state as a circle in the bottom right corner:','CONDITIONAL_SUPPORT','Direct support for finding a default launcher, but does not establish the user website actual configuration/version.'),
(WEB,'ibmcld_02855-8124-9934','Add three conversation starter messages.','ALTERNATE_INTENT','Gold helps configure home screen/conversation starters; latest user finding web chat can instead mean locating the launcher.'),
]
def main():
    root=B/'rag-g4-cloud-parent-validation8-2026-09-08'
    rankpath=root/'rerank/cases.json.gz';ranks={r['case_id']:r for r in json.load(gzip.open(rankpath,'rt'))}
    wanted={p for _,p,*_ in OBS};sources={}
    corpus=Path('/tmp/dialogpilot-mtrag-corpora-20260907/cloud.jsonl.zip')
    manifest=json.loads((B/'rag-g4-mtrag-adapter-2026-09-07/manifest.json').read_text())
    assert digest(corpus)==manifest['source']['archives']['cloud']['sha256']
    with zipfile.ZipFile(corpus) as z,z.open('cloud.jsonl') as f:
        for line in f:
            r=json.loads(line)
            if r['_id'] in wanted:sources[r['_id']]=r
    refs={}
    refpath=Path('/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl')
    for line in refpath.open():
        r=json.loads(line)
        if r['task_id'] in (LANG,WEB):refs[r['task_id']]=r['input']
    observations=[]
    for case,pid,quote,label,reason in OBS:
        text=sources[pid]['text'];assert quote in text
        full='mtrag:cloud:'+pid;r=ranks[case]
        observations.append({'case_id':case,'passage_id':full,'label':label,'reason':reason,'quote':quote,'start':text.index(quote),'end':text.index(quote)+len(quote),'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'official_gold':full in r['gold'],'rank':{a:(v['ranking'].index(full)+1 if full in v['ranking'] else None) for a,v in r['arms'].items()}})
    out=B/'rag-g4-miss-semantics2-2026-09-08';out.mkdir(exist_ok=False)
    (out/'review.json').write_text(json.dumps({'scope':'Codex nonblind diagnostic of 2 consumed cases; not independent accuracy labels; official qrels unchanged','api_calls':0,'input_sha256':digest(refpath),'ranking_sha256':digest(rankpath),'corpus_sha256':digest(corpus),'conversations':refs,'observations':observations},ensure_ascii=False,indent=2)+'\n')
    print('5 source excerpts verified; official labels unchanged; 0 API')
if __name__=='__main__':main()
