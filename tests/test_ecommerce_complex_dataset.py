import json,hashlib
from pathlib import Path
from mcp.document_chunker import DocumentChunker
ROOT=Path('data/eval/ecommerce-complex-v2')

def test_locked_evidence_and_family_partition():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    for name,digest in manifest['sha256'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
    docs={d['source_id']:d for d in json.loads((ROOT/'corpus.json').read_text())};groups=[]
    for split,n in [('dev',40),('heldout',80)]:
        inputs=json.loads((ROOT/f'{split}.inputs.json').read_text());gold=json.loads((ROOT/f'{split}.gold.json').read_text())
        assert len(inputs)==len(gold)==n and {r['id'] for r in inputs}=={r['id'] for r in gold}
        assert all(set(r)=={'id','message','history'} for r in inputs)
        groups.append({r['group_id'] for r in gold})
        for row in gold:
            assert len(row['evidence_units'])==3 and len({e['source_id'] for e in row['evidence_units']})==3
            for e in row['evidence_units']:
                body=docs[e['source_id']]['content'];assert body[e['start_char']:e['end_char']]==e['quote']
                chunks=DocumentChunker().split(body,max_tokens=512,overlap_tokens=64,source_type='markdown')
                assert any(c.start_char<=e['start_char'] and c.end_char>=e['end_char'] for c in chunks)
    assert len(groups[0])==10 and len(groups[1])==20 and not groups[0]&groups[1]

def test_complete_evidence_requires_all_three_sources():
    from scripts.report_ecommerce_complex import metrics
    units=[{'source_id':str(i),'start_char':0,'end_char':1,'quote':'x'} for i in range(3)]
    chunks=[{'source_id':str(i),'source_start_char':0,'source_end_char':1,'content':'x'} for i in range(3)]
    assert metrics(chunks[:2],units,3)['complete_r5']==0
    assert metrics(chunks[:2],units,3)['unit_r5']==2/3
    assert metrics(chunks,units,3)['complete_r5']==1
    assert metrics(chunks,units,3)['ndcg5']==1
    assert metrics([],units,3)['ndcg5']==0


def test_evidence_metrics_preserve_provenance_and_complete_unit_coverage():
    from scripts.report_ecommerce_complex import metrics
    units=[{'source_id':str(i),'start_char':0,'end_char':1,'quote':'X'} for i in range(3)]
    chunks=[{'source_id':str(i),'source_start_char':0,'source_end_char':1,'content':'X'} for i in range(3)]
    wrong={'source_id':'other-channel','source_start_char':0,'source_end_char':1,'content':'X'}
    assert metrics([wrong],units,3)['unit_r5']==0
    previous=0
    for count in range(4):
        result=metrics(chunks[:count],units,3)
        assert result['unit_r5']>=previous
        assert result['complete_r5']==int(count==3)
        assert 0<=result['ndcg5']<=1
        previous=result['unit_r5']
    assert metrics(chunks,units,3)['ndcg5']==1


def test_scoped_authoring_uses_source_declarations_and_valid_intervals():
    from scripts.build_ecommerce_scoped_corpus import applicability
    from mcp.source_document import SourceDocument
    from datetime import datetime
    import pytest
    docs=json.loads((ROOT/'corpus.json').read_text())
    for doc in docs:
        scope=applicability(doc)
        SourceDocument.create(source_id=doc['source_id'],title=doc['title'],content=doc['content'],
            **{k:datetime.fromisoformat(v) if k.startswith('effective_') else v for k,v in scope.items()})
        # Identity and relevance are not inputs to assigning applicability.
        assert applicability({**doc,'source_id':'different-id'})==scope
    assert applicability({'metadata':{'origin':'WixQA'},'content':'Any help topic'})=={}
    with pytest.raises(ValueError):
        applicability({'source_id':'unknown','metadata':{},'content':'unknown source'})
