import asyncio
from dataclasses import asdict
from datetime import datetime

import pytest

from evaluation.rag_applicability_dev import applicability_development
from scripts.run_rag_tool_calibration import CandidateScopeProbe, CandidateProbeComplete
from application.knowledge_retriever import KnowledgeCandidateResult
from application.hybrid_retrieval import RetrievalStatus
from tests.test_knowledge_retriever import _request


def test_every_gold_span_has_an_applicable_unwithdrawn_source_revision():
    docs, cases, options, forbidden, withdrawn = applicability_development()
    assert len({c.case_id for c in cases}) == len(cases)
    for case in cases:
        scope = options[case.case_id]
        instant = datetime.fromisoformat(scope['as_of'])
        for gold in case.evidence:
            matches = []
            for doc in docs:
                meta = doc.metadata
                if doc.document_id != gold.document_id or doc.document_id in withdrawn:
                    continue
                if doc.content[gold.start_char:gold.end_char] != gold.quote:
                    continue
                if datetime.fromisoformat(meta['effective_from']) > instant:
                    continue
                if meta.get('effective_to') and instant >= datetime.fromisoformat(meta['effective_to']):
                    continue
                if any(scope.get('applicable_'+key) and meta.get(key,default) not in (default,scope['applicable_'+key]) for key,default in [('region','global'),('channel','global'),('product','')]):
                    continue
                matches.append(doc)
            assert matches, case.case_id
            assert gold.document_id not in forbidden[case.case_id]


def test_probe_changes_only_business_applicability_and_preserves_security():
    request = _request(query_mode='RESOLVED',history=(),applicable_region='CN',applicable_channel='web',applicable_product='M10')
    calls = []
    class Source:
        async def search_variants_async(self, req, variants, *, top_k):
            calls.append((req, variants, top_k))
            return KnowledgeCandidateResult(RetrievalStatus.NO_EVIDENCE)
    with pytest.raises(CandidateProbeComplete) as captured:
        asyncio.run(CandidateScopeProbe(Source()).retrieve(request))
    assert calls[0][0] is request
    first, second = asdict(calls[0][0]), asdict(calls[1][0])
    assert {key for key in first if first[key] != second[key]} == {'as_of','applicable_region','applicable_channel','applicable_product'}
    assert calls[0][1:] == calls[1][1:]
    assert set(captured.value.result) == {'scoped','omitted_applicability'}


def test_candidate_probe_transport_has_zero_external_call_budget():
    from types import SimpleNamespace
    from scripts.run_rag_tool_calibration import CaptureClient
    class Messages:
        async def create(self, **kwargs):
            pytest.fail('candidate probe must not invoke external inference')
    transport = SimpleNamespace(messages=Messages(), beta=SimpleNamespace(messages=Messages()))
    client = CaptureClient(transport, limit=0)
    for channel in (client.messages, client.beta.messages):
        with pytest.raises(RuntimeError, match='call budget exhausted'):
            asyncio.run(channel.create(model='unused', messages=[]))
    assert not client.calls
