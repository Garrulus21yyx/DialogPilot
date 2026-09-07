"""The experimental PG adapter preserves source authority and route budget."""
from types import SimpleNamespace
import pytest
import asyncio
from application.knowledge_retriever import KnowledgeCandidateResult
from application.hybrid_retrieval import RetrievalStatus
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource
from scripts.run_rag_membership_flash_pair import MembershipSource


async def _disjoint_routes_keep_source_fields_and_share_budget(monkeypatch):
    rows = tuple({'chunk_id':f'{kind}{i}', 'source_revision':'r1', 'ranks':{f'raw:{kind}':i}} for kind in ('vector','bm25') for i in range(1,21))
    union = KnowledgeCandidateResult(RetrievalStatus.OK, rows)
    source = object.__new__(MembershipSource)
    async def capture(*a, **kw):
        assert kw == {'dense_k':20,'lexical_k':20}
        return union
    source.capture_source_rankings_async = capture
    async def original(*a, **kw):
        return KnowledgeCandidateResult(RetrievalStatus.OK, rows[20:])
    monkeypatch.setattr(PostgresKnowledgeCandidateSource,'search_variants_async',original)
    request = SimpleNamespace(source_type_hints=(),region_hints=(),as_of_end=None)
    for balanced in (False, True):
        source.balanced = balanced
        result = await source.search_variants_async(request,[('raw','query',1.)],top_k=20)
        assert len(result.candidates)==20
        assert all(row['source_revision']=='r1' for row in result.candidates)
        assert sum(row['chunk_id'].startswith('vector') for row in result.candidates)==(10 if balanced else 0)


async def _unavailable_union_is_not_turned_into_partial_evidence():
    source=object.__new__(MembershipSource)
    failure=KnowledgeCandidateResult(RetrievalStatus.UNAVAILABLE,detail_code='RETRIEVAL_BRANCH_UNAVAILABLE')
    async def capture(*a, **kw):return failure
    source.capture_source_rankings_async=capture
    result=await source.search_variants_async(SimpleNamespace(source_type_hints=(),region_hints=(),as_of_end=None),[('raw','query',1.)],top_k=20)
    assert result is failure


def test_disjoint_routes_keep_source_fields_and_share_budget(monkeypatch):
    asyncio.run(_disjoint_routes_keep_source_fields_and_share_budget(monkeypatch))


def test_unavailable_union_is_not_turned_into_partial_evidence():
    asyncio.run(_unavailable_union_is_not_turned_into_partial_evidence())


def test_saved_flash_sample_and_visible_evidence_are_auditable():
    import gzip
    import hashlib
    import json
    from pathlib import Path
    root=Path('artifacts/eval/rag-g3-flash-pair12-2026-09-07')
    snapshot=json.loads(gzip.decompress(Path('artifacts/eval/rag-known-miss-g1-budget-diagnostic-2026-09-07/input-datasets.json.gz').read_bytes()))['doc2dial-rag-mini-dev-v1']
    cases=list(map(json.loads,snapshot['cases.jsonl'].splitlines()))
    selected=sorted(cases,key=lambda r:hashlib.sha256(r['id'].encode()).hexdigest())[:12]
    expected=[c['id'] for c in selected]
    docs={d['id']:d['content'] for d in map(json.loads,snapshot['corpus.jsonl'].splitlines())}
    rows=[json.loads(line) for line in gzip.decompress((root/'cases.jsonl.gz').read_bytes()).splitlines()]
    assert len(rows)==24
    for arm in ('baseline','balanced'):
        assert [r['case_id'] for r in rows if r['arm']==arm]==expected
    gold={c['id']:c['evidence'] for c in selected}
    for r in rows:
        assert r['status']=='OK' and r['fallback'] is False
        assert set(r['ordered_ids'])=={c['chunk_id'] for c in r['candidates']}
        evidence=json.loads(r['tool_message'])['evidence']
        for e in evidence:
            s=e['source']
            assert e['text']==docs[s['source_id']][s['start_char']:s['end_char']]
        assert r['visible_complete']==all(any(e['source']['source_id']==g['document_id'] and e['source']['start_char']<=g['start_char'] and e['source']['end_char']>=g['end_char'] for e in evidence) for g in gold[r['case_id']])
