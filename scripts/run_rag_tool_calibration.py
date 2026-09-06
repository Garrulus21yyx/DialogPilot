#!/usr/bin/env python3
"""Fixed-query development calibration through the real knowledge handler.

Uses a newly created PostgreSQL database, production BGE/PG retrieval, reranker,
model-visible evidence serialization and GroundedAnswerGenerator. It does not
exercise HTTP authentication, conversation planning or business-tool execution.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
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
    def __init__(self, transport):
        self.transport = transport
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
            if len(self.owner.calls) >= 80:
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


async def evaluate(args, database_url):
    import api.main as api
    values = {k: str(v) for k, v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    options = dict(api_key=values['ANTHROPIC_API_KEY'], max_retries=0, timeout=60)
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
        if args.distractors:
            docs = RagDataset.load(args.distractors).documents + docs
        store = PostgresKnowledgeStore(platform, tenant_id='rag-tool-dev', embedding_provider=embedding)
        documents = tuple(SourceDocument.create(source_id=d.document_id, title=d.title, content=d.content, source_type='text') for d in docs)
        batch = OFFLINE_KNOWLEDGE_INGEST_BUDGET.max_sources_per_batch
        for start in range(0, len(documents), batch):
            store.add_documents(documents[start:start+batch])
        with platform.transaction() as conn:
            conn.execute('INSERT INTO dialogpilot_app.conversations (tenant_id,user_id,conversation_id) VALUES (%s,%s,%s)', ('rag-tool-dev','eval-user','eval-conversation'))
        source = PostgresKnowledgeCandidateSource(
            backend=PostgresHybridBackend(retrieval), generations=PostgresRetrievalGenerationRegistry(platform),
            pool=retrieval, embed_query=store.embed_query,
        )
        async with AsyncAnthropic(**options) as transport:
            client = CaptureClient(transport)
            reranker = ToolManagerRerankerAdapter(SimpleNamespace(_result_reranker=ResultReranker(client, policy.profile(ModelRole.RERANK))))
            api._knowledge_store, api._postgres_pool = store, platform
            api._knowledge_retriever = KnowledgeRetriever(
                candidate_source=source, transformer=QueryTransformer(client, policy.profile(ModelRole.REWRITE)),
                reranker=reranker, evidence_validator=PostgresKnowledgeEvidenceValidator(source),
            )
            generator = GroundedAnswerGenerator(client, policy.profile(ModelRole.SYNTHESIS))
            manifest = {'scope': __doc__, 'documents': len(docs), 'cases': len(cases),
                        'generation': asdict(store.active_generation()), 'embedding_profile': asdict(embedding.profile),
                        'reranker_profile': policy.profile(ModelRole.RERANK).to_dict(),
                        'generation_profile': policy.profile(ModelRole.SYNTHESIS).to_dict(),
                        'max_api_calls': 80, 'sdk_retries': 0,
                        'fixed_query_override': {'synthetic:elliptic': '耳机已拆封，非质量原因可以退货吗？'}}
            (args.output/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str)+'\n')
            for case in cases:
                query = manifest['fixed_query_override'].get(case.case_id, case.query)
                before = len(client.calls)
                start = time.perf_counter()
                result = await api._knowledge_tool_handler({'query': query}, {
                    'tenant_id':'rag-tool-dev', 'user_id':'eval-user', 'conv_id':'eval-conversation',
                    'authorization_fingerprint':'isolated-eval-authorized', 'cache_scope':'fixed-query-dev-v1',
                    'retrieval_policy': {'query_expansion_count':0, 'expansion_query_weight':0.0},
                })
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
    args = p.parse_args()
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
