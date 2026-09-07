import json
from pathlib import Path
import zipfile
import pytest
from scripts.adapt_mtrag_retrieval_dataset import build, digest, DOMAINS
from evaluation.rag_pipeline.dataset import RagDataset


def fixture(tmp):
    cache, corpora = tmp/'cache', tmp/'corpora'
    cache.mkdir(); corpora.mkdir()
    groups, refs = [], []
    for domain in DOMAINS:
        qid=domain+'-q'
        groups.append({'task_ids':[qid],'conversation_id':domain+'-c','domain':domain,'split':'dev'})
        refs.append({'task_id':qid,'conversation_id':domain+'-c','Answerability':['PARTIAL']})
        for mode in ('lastturn','questions','rewrite'):
            (cache/f'{domain}-{mode}.jsonl').write_text(json.dumps({'_id':qid,'text':mode+' question'})+'\n')
        (cache/f'{domain}-qrels.tsv').write_text(f'query-id\tcorpus-id\tscore\n{qid}\tp1\t1\n')
        with zipfile.ZipFile(corpora/f'{domain}.jsonl.zip','w') as z:
            z.writestr(f'{domain}.jsonl','\n'.join(json.dumps(r) for r in [
                {'_id':'p1','text':'relevant passage'}, {'_id':'p2','text':'distractor'}, {'_id':'empty','text':' '}]))
    (cache/'reference.jsonl').write_text('\n'.join(map(json.dumps,refs)))
    lock=tmp/'lock.json'
    lock.write_text(json.dumps({'revision':'fixture','groups':groups,'source_files':{p.name:{'sha256':digest(p)} for p in cache.iterdir()}}))
    return cache,corpora,lock


@pytest.mark.parametrize("mode", ["lastturn", "questions", "rewrite"])
def test_full_corpus_identity_and_coarse_labels(tmp_path,mode):
    args=fixture(tmp_path)
    result=build(*args,tmp_path/'out',mode=mode)
    data=RagDataset.load(tmp_path/'out')
    assert result['document_count']==8 and result['case_count']==4
    assert len({d.document_id for d in data.documents})==8  # IDs are domain-scoped
    assert all(e.granularity=='document' for c in data.cases for e in c.evidence)
    assert all('partial' in c.query_types for c in data.cases)
    assert all(x['excluded_empty_ids']==['empty'] for x in result['source']['stats'].values())
    assert all(c.query == mode+' question' for c in data.cases)
    assert all(c.history==() for c in data.cases)  # no invented speaker history


@pytest.mark.parametrize('bad', ['missing','duplicate'])
def test_qrel_cannot_silently_lose_or_alias_a_passage(tmp_path,bad):
    cache,corpora,lock=fixture(tmp_path)
    with zipfile.ZipFile(corpora/'clapnq.jsonl.zip','w') as z:
        row={'_id':'p1','text':'' if bad=='missing' else 'text'}
        z.writestr('clapnq.jsonl','\n'.join([json.dumps(row)]*(2 if bad=='duplicate' else 1)))
    with pytest.raises(ValueError,match='missing qrel|duplicate source'):
        build(cache,corpora,lock,tmp_path/'out')


@pytest.mark.parametrize('separator', ['\u0085','\u2028','\u2029'])
def test_dataset_roundtrip_preserves_unicode_separators(tmp_path,separator):
    from evaluation.rag_pipeline.dataset import write_dataset
    text='before'+separator+'after'
    write_dataset(tmp_path,dataset_id='unicode-roundtrip',
        documents=[{'id':'d','content':text}],
        cases=[{'id':'q','group_id':'g','split':'dev','query':text,
                'evidence':[{'document_id':'d','start_char':0,'end_char':len(text),'quote':text}]}],source={})
    d=RagDataset.load(tmp_path)
    assert d.documents[0].content == d.cases[0].query == d.cases[0].evidence[0].quote == text
