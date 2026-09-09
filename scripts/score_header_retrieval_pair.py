"""Score fixed dev source evidence across strategy-specific chunk universes."""
import gzip,hashlib,json,random
from pathlib import Path
from mcp.document_chunker import DocumentChunker
from scripts.report_ecommerce_complex import covers,metrics

root=Path('artifacts/eval/header-retrieval-pair40-2026-09-09-v3')
gold={r['id']:r for r in json.loads(Path('data/eval/ecommerce-complex-v2/dev.gold.json').read_text())}
docs={d['source_id']:d for d in json.loads(Path('artifacts/eval/ecommerce-complex-v2-scoped-corpus-v2/corpus.json').read_text())}
strategies=['structure_aware','markdown_headers'];scored=[];queries={};summaries={}
for strategy in strategies:
    rows=[json.loads(l) for l in gzip.open(root/strategy/'pure-cases.jsonl.gz','rt')]
    assert len(rows)==40 and {r['id'] for r in rows}==set(gold)
    complete=json.loads((root/strategy/'pure-completion.json').read_text());assert complete['api_calls']==0
    # Other sources cannot cover these gold source IDs; all potentially relevant
    # chunks are reconstructed for each strategy's binary nDCG denominator.
    universe=[]
    for source_id in {u['source_id'] for g in gold.values() for u in g['evidence_units']}:
        d=docs[source_id]
        universe.extend({'source_id':source_id,'source_start_char':c.start_char,'source_end_char':c.end_char,'content':c.content}
            for c in DocumentChunker().split(d['content'],max_tokens=512,overlap_tokens=64,strategy=strategy,source_type=d['metadata']['source_type']))
    for r in rows:
        units=gold[r['id']]['evidence_units']
        cs=r['retrieval'][-1]['fused_candidates'] if r['retrieval'] else []
        ordered=r['rerank'][-1]['ordered_ids'] if r['rerank'] else []
        mapping={c['chunk_id']:c for c in cs}
        wire=[{'source_id':e['source']['source_id'],'source_start_char':e['source']['start_char'],'source_end_char':e['source']['end_char'],'content':e['text']} for e in r['wire'].get('evidence',[])]
        for c in cs+wire:assert docs[c['source_id']]['content'][c['source_start_char']:c['source_end_char']]==c['content']
        if ordered:assert set(ordered)==set(mapping)
        pack=r['result'].get('evidence_pack')
        if pack:
            policy=pack['retrieval_policy']
            for key,value in {'candidate_k':20,'top_k':5,'context_max_tokens':2600,
                'vector_weight':0.5,'lexical_weight':0.5,'rrf_k':10,'query_expansion_count':0}.items():
                assert policy[key]==value
            assert len(wire)<=5
        signature=(r['query'],r['original'],r['history'],
            [(route['query'],route['variants'],route['top_k'],route['rrf_k']) for route in r['retrieval']])
        if strategy==strategies[0]:queries[r['id']]=signature
        else:assert queries[r['id']]==signature,'query or variant policy changed'
        for route in r['retrieval']:
            scope=route['request_scope'];assert scope['applicable_region']=='CN' and scope['applicable_channel']=='web'
            assert str(scope['as_of']).startswith('2026-09-10 00:00:00')
        relevant=sum(any(covers(c,u) for u in units) for c in universe)
        scored.append({'id':r['id'],'strategy':strategy,'status':r['result']['status'],
          **{name:metrics(x,units,relevant) for name,x in [('candidate',cs),('rerank',[mapping[k] for k in ordered]),('wire',wire)]},
          'wrong_scope':sum(c['source_id'] in gold[r['id']]['forbidden_sources'] or c['source_id'].startswith('complex:scope-negative:') for c in wire),
          'ms':r['measured_ms'],'chunk_relevant_count':relevant,
          'rerank_fallback':any(x['fallback'] for x in r['rerank']),
          'rerank_pairs':sum(len(x['ordered_ids']) for x in r['rerank']),
          'missing_wire_units':[{'unit_id':u['unit_id'],'quote':u['quote'],
            'candidate_rank':next((i for i,c in enumerate(cs,1) if covers(c,u)),None),
            'rerank_rank':next((i for i,k in enumerate(ordered,1) if covers(mapping[k],u)),None)}
            for u in units if not any(covers(c,u) for c in wire)]})
    rr=[r for r in scored if r['strategy']==strategy]
    summaries[strategy]={stage:{k:sum(r[stage][k] for r in rr)/40 for k in rr[0][stage]} for stage in ['candidate','rerank','wire']}
    summaries[strategy]['failures']=[r['id'] for r in rr if r['status']!='OK'];summaries[strategy]['wrong_scope_chunks']=sum(r['wrong_scope'] for r in rr)
    summaries[strategy]['rerank_fallbacks']=sum(r['rerank_fallback'] for r in rr)
    summaries[strategy]['rerank_pairs']=sum(r['rerank_pairs'] for r in rr)
changes=[]
for cid in gold:
    b,c=[next(x for x in scored if x['id']==cid and x['strategy']==s) for s in strategies]
    changes.append({'id':cid,'delta':c['wire']['complete_r5']-b['wire']['complete_r5'],
        'unit_delta':c['wire']['unit_r5']-b['wire']['unit_r5'],
        'before':b['wire']['complete_r5'],'after':c['wire']['complete_r5'],'both_ok':b['status']=='OK' and c['status']=='OK'})
groups={}
for c in changes:groups.setdefault(gold[c['id']]['group_id'],[]).append(c['delta'])
means=[sum(x)/len(x) for x in groups.values()];rng=random.Random(20260909)
boots=sorted(sum(rng.choice(means) for _ in means)/len(means) for _ in range(5000))
report={'cases':40,'groups':len(groups),'summary':summaries,'rescued':[c['id'] for c in changes if c['delta']>0],
        'hurt':[c['id'] for c in changes if c['delta']<0],'group_bootstrap_95':[boots[124],boots[4874]],
        'partial_coverage_regressions':[c['id'] for c in changes if c['unit_delta']<0],
        'note':'Consumed synthetic dev; actual PG retrieval/CE/wire, no generation. nDCG uses strategy-specific chunk relevance; complete source evidence is primary.',
        'query_and_scope_equivalence':True,'source_span_audit':True,'api_calls':0}
(root/'scored.json').write_text(json.dumps(scored,ensure_ascii=False,indent=2)+'\n');(root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps(report,ensure_ascii=False,indent=2))
