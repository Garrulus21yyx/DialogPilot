#!/usr/bin/env python3
"""Fixed-query development calibration through the real knowledge handler.

Uses a newly created PostgreSQL database, production BGE/PG retrieval, reranker,
model-visible evidence serialization and GroundedAnswerGenerator. It does not
exercise HTTP authentication, conversation planning or business-tool execution.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg import sql
from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from evaluation.rag_ecommerce_dev import synthetic_development
from evaluation.rag_pipeline.dataset import RagDataset
from infrastructure.postgres import PostgresMigrationRunner, PostgresPool, PostgresPoolConfig
from infrastructure.postgres_knowledge_store import PostgresKnowledgeStore
from infrastructure.bge_m3_embedding import BGEM3EmbeddingConfig, LocalBGEM3EmbeddingProvider
from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
from infrastructure.retrieval_postgres import RetrievalPostgresPool, RetrievalPoolConfig, PostgresRetrievalGenerationRegistry
from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource, PostgresKnowledgeEvidenceValidator
from infrastructure.knowledge_retriever_adapters import ToolManagerRerankerAdapter
from application.knowledge_retriever import KnowledgeRetriever
from application.cost_budget_policy import OFFLINE_KNOWLEDGE_INGEST_BUDGET
from mcp.source_document import SourceDocument
from mcp.query_transformer import QueryTransformer
from mcp.result_reranker import ResultReranker
from mcp.grounded_answer_generator import GroundedAnswerGenerator
from mcp.context_packer import ContextCandidate
from mcp.tool_manager import MCPToolManager, ToolResult


class CaptureClient:
    def __init__(self, transport, *, limit=80):
        self.transport = transport
        self.limit = limit
        self.calls = []
        self.messages = self.Proxy(self, transport.messages)
        self.beta = SimpleNamespace(messages=self.Proxy(self, transport.beta.messages))

    def __getattr__(self, name):
        return getattr(self.transport, name)

    class Proxy:
        def __init__(self, owner, messages):
            self.owner, self.messages = owner, messages

        def __getattr__(self, name):
            return getattr(self.messages, name)

        async def create(self, **request):
            if len(self.owner.calls) >= self.owner.limit:
                raise RuntimeError('calibration call budget exhausted')
            row = {'request': request}
            self.owner.calls.append(row)
            start = time.perf_counter()
            try:
                response = await self.messages.create(**request)
                row['response'] = response.model_dump(mode='json')
                return response
            except Exception as exc:
                row['error_type'] = type(exc).__name__
                raise RuntimeError('calibration API failure: ' + type(exc).__name__) from None
            finally:
                row['latency_ms'] = (time.perf_counter() - start) * 1000


class CandidateProbeComplete(Exception):
    def __init__(self, result):
        self.result = result


class CandidateScopeProbe:
    """Intercept a real handler-built request and measure candidate retrieval only."""
    def __init__(self, source):
        self.source = source

    async def retrieve(self, request):
        variants = [('raw',request.query,1.0)]
        scoped = await self.source.search_variants_async(request, variants, top_k=request.policy.candidate_k)
        omitted = replace(request, applicable_region=None, applicable_channel=None,
                          applicable_product=None, as_of=datetime(2026,6,1,tzinfo=timezone.utc))
        unscoped = await self.source.search_variants_async(omitted, variants, top_k=request.policy.candidate_k)
        raise CandidateProbeComplete({'scoped':asdict(scoped), 'omitted_applicability':asdict(unscoped)})


async def evaluate(args, database_url):
    import api.main as api
    values = {k: str(v) for k, v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    options = dict(api_key='unused-local-probe' if args.candidate_scope_probe else values['ANTHROPIC_API_KEY'], max_retries=0, timeout=60)
    if policy.base_url:
        options['base_url'] = policy.base_url
    model = args.model.resolve()
    digest = hashlib.file_digest((model/'pytorch_model.bin').open('rb'), 'sha256').hexdigest()
    embedding = LocalBGEM3EmbeddingProvider(BGEM3EmbeddingConfig(
        model, model.name, digest, device='cuda', batch_size=16,
    ))
    PostgresMigrationRunner(database_url).upgrade()
    platform = PostgresPool(PostgresPoolConfig(database_url, min_size=1, max_size=3))
    retrieval = RetrievalPostgresPool(RetrievalPoolConfig(database_url, min_size=1, max_size=3))
    platform.open()
    retrieval.open()
    source = None
    try:
        docs, cases = synthetic_development()
        query_options, forbidden_sources, withdrawn_sources = {}, {}, ()
        if args.scenario == 'applicability':
            from evaluation.rag_applicability_dev import applicability_development
            docs, cases, query_options, forbidden_sources, withdrawn_sources = applicability_development()
        if args.distractors:
            docs = RagDataset.load(args.distractors).documents + docs
        store = PostgresKnowledgeStore(platform, tenant_id='rag-tool-dev', embedding_provider=embedding)
        documents = tuple(SourceDocument.create(
            source_id=d.document_id, title=d.title, content=d.content,
            source_type=d.metadata.get('source_type','text'),
            **{key: d.metadata[key] for key in ('region','channel','product') if key in d.metadata},
            **{key: datetime.fromisoformat(d.metadata[key]) for key in ('effective_from','effective_to') if d.metadata.get(key)},
        ) for d in docs)
        batch = OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_sources_per_batch
        revisions = []
        for start in range(0, len(documents), batch):
            revisions.extend(store.import_documents(documents[start:start+batch]).revisions)
        for revision in revisions:
            if revision.source_id in withdrawn_sources:
                store.withdraw_revision(revision.source_id, revision.revision_id, reason='synthetic evaluation withdrawal')
        with platform.transaction() as conn:
            conn.execute('INSERT INTO dialogpilot_app.conversations (tenant_id,user_id,conversation_id) VALUES (%s,%s,%s)', ('rag-tool-dev','eval-user','eval-conversation'))
        source = PostgresKnowledgeCandidateSource(
            backend=PostgresHybridBackend(retrieval), generations=PostgresRetrievalGenerationRegistry(platform),
            pool=retrieval, embed_query=store.embed_query,
        )
        async with AsyncAnthropic(**options) as transport:
            client = CaptureClient(transport, limit=0 if args.candidate_scope_probe else args.max_api_calls)
            reranker = ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(client, policy.profile(ModelRole.RERANK))))
            api._knowledge_store, api._postgres_pool = store, platform
            api._knowledge_retriever = KnowledgeRetriever(
                candidate_source=source, transformer=QueryTransformer(client, policy.profile(ModelRole.REWRITE)),
                reranker=reranker, evidence_validator=PostgresKnowledgeEvidenceValidator(source),
            )
            if args.candidate_scope_probe:
                api._knowledge_retriever = CandidateScopeProbe(source)
            generator = GroundedAnswerGenerator(client, policy.profile(ModelRole.SYNTHESIS))
            manifest = {'scope': 'candidate-only: handler request construction + actual PG retrieval; no handler completion or generation' if args.candidate_scope_probe else __doc__, 'scenario':args.scenario, 'documents': len(docs), 'cases': len(cases),
                        'query_options':query_options, 'forbidden_sources':forbidden_sources, 'withdrawn_sources':withdrawn_sources,
                        'source_documents':[asdict(d) for d in docs], 'case_definitions':[asdict(c) for c in cases],
                        'generation': asdict(store.active_generation()), 'embedding_profile': asdict(embedding.profile),
                        'reranker_profile': policy.profile(ModelRole.RERANK).to_dict(),
                        'generation_profile': policy.profile(ModelRole.SYNTHESIS).to_dict(),
                        'max_api_calls': client.limit, 'sdk_retries': 0,
                        'fixed_query_override': {'synthetic:elliptic': '耳机已拆封，非质量原因可以退货吗？'}}
            if args.mixed_business:
                manifest.update(scope='Mixed application evaluation; see mixed-manifest.json for executed cases and scope',
                                cases=len(args.mixed_definitions) if args.mixed_definitions else 5, case_definitions=args.mixed_definitions or [], fixed_query_override={})
            (args.output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str)+'\n')
            if args.mixed_business:
                from evaluation.rag_mixed_business import run_mixed
                await run_mixed(platform=platform,store=store,client=client,policy=policy,generator=generator,
                                provider_config=options, output=args.output,handler=api._knowledge_tool_handler,case_definitions=args.mixed_definitions)
                (args.output/'completion.json').write_text(json.dumps({'scope':'mixed application development','cases':manifest['cases'],'api_calls':len(client.calls)})+'\n')
                return
            for case in cases:
                query = manifest['fixed_query_override'].get(case.case_id, case.query)
                before = len(client.calls)
                start = time.perf_counter()
                try:
                    result = await api._knowledge_tool_handler({'query': query, **query_options.get(case.case_id,{})}, {
                        'tenant_id':'rag-tool-dev', 'user_id':'eval-user', 'conv_id':'eval-conversation',
                        'authorization_fingerprint':'isolated-eval-authorized', 'cache_scope':'fixed-query-dev-v1',
                        'retrieval_policy': {'query_expansion_count':0, 'expansion_query_weight':0.0},
                    })
                except CandidateProbeComplete as probe:
                    row = {'case_id':case.case_id, 'query':query, 'query_options':query_options.get(case.case_id,{}),
                           'gold_evidence':[asdict(g) for g in case.evidence],
                           'forbidden_sources':forbidden_sources.get(case.case_id,[]),
                           'candidate_probe':probe.result, 'api_calls':[]}
                    with (args.output/'cases.jsonl').open('a') as stream:
                        stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
                    print(case.case_id, 'candidate probe captured', flush=True)
                    continue
                wire = MCPToolManager._render_for_model(None, ToolResult(True, result, 'knowledge_search', authority='knowledge.active_source'))
                visible = json.loads(wire)
                contexts = tuple(ContextCandidate(
                    chunk_id=e['evidence_id'], document_id=e['source']['source_id'], text=e['text'],
                    start_char=e['source']['start_char'], end_char=e['source']['end_char'], title=e['title'],
                    applicability=tuple(e['source'].get('applicability',{}).items()),
                ) for e in visible.get('evidence',[]))
                answer = await generator.generate(query, contexts)
                covered = [any(c.document_id==gold.document_id and c.start_char<=gold.start_char and c.end_char>=gold.end_char and gold.quote in c.text for c in contexts) for gold in case.evidence]
                row = {'case_id':case.case_id, 'query':query, 'tool_result':result, 'tool_message':visible,
                       'gold_evidence':[asdict(g) for g in case.evidence], 'complete_visible_evidence':all(covered),
                       'forbidden_sources':forbidden_sources.get(case.case_id,[]),
                       'returned_forbidden_sources':sorted({c.document_id for c in contexts} & set(forbidden_sources.get(case.case_id,[]))),
                       'answer':asdict(answer), 'api_calls':client.calls[before:],
                       'latency_ms':(time.perf_counter()-start)*1000}
                with (args.output/'cases.jsonl').open('a') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, default=str)+'\n')
                print(case.case_id, result['status'], all(covered), flush=True)
            (args.output/'completion.json').write_text(json.dumps({'cases':len(cases),'api_calls':len(client.calls),'answer_semantics':'unreviewed'})+'\n')
    finally:
        if source is not None:
            source.close()
        retrieval.close()
        platform.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--distractors', type=Path)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--mixed-business', action='store_true')
    mode.add_argument('--candidate-scope-probe', action='store_true', help='No inference: paired candidate retrieval with/without request applicability')
    p.add_argument('--mixed-case-file',type=Path)
    p.add_argument('--max-api-calls',type=int,default=80)
    p.add_argument('--scenario', choices=('basic','applicability'), default='basic')
    args = p.parse_args()
    if not 0 < args.max_api_calls <= 400:
        raise ValueError('max API calls must be between 1 and 400')
    args.mixed_definitions = None
    if args.mixed_case_file:
        if not args.mixed_business:
            raise ValueError('mixed case file requires mixed business mode')
        args.mixed_definitions = json.loads(args.mixed_case_file.read_text())
        rows = args.mixed_definitions
        if (not isinstance(rows,list) or not 1 <= len(rows) <= 40
                or any(not isinstance(c,dict) or not isinstance(c.get('case_id'),str)
                       or not c['case_id'] or not isinstance(c.get('message'),str) or not c['message']
                       or not isinstance(c.get('history'),list) or len(c['history']) % 2
                       or any(not isinstance(t,str) for t in c['history']) for c in rows)
                or len({c['case_id'] for c in rows}) != len(rows)):
            raise ValueError('invalid mixed case definitions')
    if args.output.exists():
        raise ValueError('output must be new')
    base = os.environ['TEST_DATABASE_URL']  # No production .env database fallback.
    parsed = urlsplit(base)
    name = 'dialogpilot_rag_dev_' + uuid.uuid4().hex[:12]
    args.output.mkdir(parents=True)
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    database_url = urlunsplit((parsed.scheme,parsed.netloc,'/'+name,parsed.query,parsed.fragment))
    try:
        asyncio.run(evaluate(args, database_url))
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', (name,))
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))


if __name__ == '__main__':
    main()
