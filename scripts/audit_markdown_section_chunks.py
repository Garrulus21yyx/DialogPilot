"""Source-only development audit; never treats containment as retrieval accuracy."""
import json,hashlib
from pathlib import Path
from mcp.document_chunker import DocumentChunker

root=Path('artifacts/eval/markdown-sections-dev-2026-09-09');root.mkdir(parents=True,exist_ok=True)
corpus_path=Path('data/eval/ecommerce-complex-v2/corpus.json')
gold_path=Path('data/eval/ecommerce-complex-v2/dev.gold.json')
docs=json.loads(corpus_path.read_text());gold=json.loads(gold_path.read_text())
results={}
for strategy in ['structure_aware','markdown_headers']:
    bysource={}
    for d in docs:
        chunks=DocumentChunker().split(d['content'],max_tokens=512,overlap_tokens=64,
                                       strategy=strategy,source_type=d.get('source_type','markdown'))
        positions=set()
        for c in chunks:
            assert d['content'][c.start_char:c.end_char]==c.content
            positions.update(range(c.start_char,c.end_char))
        assert len(positions)==len(d['content'])
        bysource[d['source_id']]=chunks
    units=[]
    for case in gold:
        for u in case['evidence_units']:
            units.append(any(c.start_char<=u['start_char'] and c.end_char>=u['end_char'] and u['quote'] in c.content
                             for c in bysource[u['source_id']]))
    current=[c for d in docs if d['source_id'].startswith('complex:current:') for c in bysource[d['source_id']]]
    topics=[r[0] for r in json.loads(Path('data/eval/ecommerce-complex-v2/rules.json').read_text())]
    # Heading membership, not keyword matches inside unrelated narrative.
    counts=[sum(('## '+topic+'\n') in c.content for topic in topics) for c in current]
    results[strategy]={'all_documents_chunks':sum(map(len,bysource.values())),
        'current_policy_chunks':len(current),'multiple_product_headings_chunks':sum(n>1 for n in counts),
        'max_product_headings_in_one_chunk':max(counts),'contained_evidence_units':sum(units),'evidence_units':len(units)}
report={'scope':'40 development cases, source containment only; no retrieval/rerank/generation run',
        'budget':{'max_tokens':512,'overlap_tokens':64},'results':results,'api_calls':0,
        'hashes':{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [corpus_path,gold_path,Path('mcp/document_chunker.py')]}}
(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
