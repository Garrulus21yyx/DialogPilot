"""Pure knowledge retrieval replay; inputs and labels live in separate files."""
import gzip
import json
import time
from pathlib import Path
from mcp.query_transformer import QueryTransformer
from core.model_policy import ModelRole
from mcp.tool_manager import MCPToolManager, ToolResult


class RerankCapture:
    def __init__(self, delegate):
        self.delegate = delegate
        self.records = []
        self.version = delegate.version

    async def rerank(self, query, candidates):
        ids, fallback = await self.delegate.rerank(query, candidates)
        self.records.append({'query': query, 'ordered_ids': ids, 'fallback': fallback})
        return ids, fallback


class MemoTransformer:
    """Capture and retrieval reuse one identical query transformation."""
    def __init__(self, delegate):
        self.delegate, self.cache = delegate, {}
    async def standalone(self, query, history=()):
        key=(query,tuple(history))
        if key not in self.cache:
            self.cache[key]=await self.delegate.standalone(query,history)
        return self.cache[key]
    async def expand(self, query, **kwargs):
        return await self.delegate.expand(query,**kwargs)


class ReplayTransformer:
    def __init__(self, path):
        self.queries = {}
        for row in map(json.loads, gzip.decompress(Path(path).read_bytes()).splitlines()):
            if row['arm']=='standalone_raw':
                key=(row['original'],tuple(f'{role}: {text}' for role,text in row['history']))
                self.queries[key]=(row['query'],row['rewrite_error'])
    async def standalone(self, query, history=()):
        return self.queries[(query,tuple(history))]


def scope_fixture_options(case):
    """This fixed benchmark explicitly states scope in a user turn, including history."""
    user_turns=[text for role,text in case['history'] if role=='user']+[case['message']]
    declaration='我只问2026年6月1日起中国大陆官网现行规则，不问经销商或香港渠道'
    if not any(declaration in text for text in user_turns):
        raise ValueError('scope fixture must explicitly declare CN/web in user context')
    return {'applicable_region':'CN','applicable_channel':'web'}


async def run(*, inputs, output, retrieve, client, policy, source, reranker, transformer, scope_pair=False, only_scope_filtered=False, as_of=None):
    cases = json.loads(Path(inputs).read_text())
    from datetime import datetime, timezone
    evaluation_time=as_of or datetime.now(timezone.utc)
    if only_scope_filtered and not scope_pair:
        raise ValueError("filtered-only requires scope pair contract")
    rows = []
    for case in cases:
        history = [f'{role}: {text}' for role, text in case['history']]
        before = len(client.calls)
        rewritten, error = await transformer.standalone(case['message'], history)
        rewrite_calls = client.calls[before:]
        concat = '\n'.join([*history, 'user: '+case['message']]) if history else case['message']
        arms=[('scope_unfiltered',rewritten),('scope_filtered',rewritten)] if scope_pair else [('history_concat', concat), ('standalone_raw', rewritten)]
        if only_scope_filtered:
            arms=[('scope_filtered', rewritten)]
        known_scope=scope_fixture_options(case) if scope_pair else {}
        for arm, query in arms:
            scope={'as_of':evaluation_time} if scope_pair else {}
            if arm=='scope_filtered':scope.update(known_scope)
            start = time.perf_counter()
            rs, rk = len(source.records), len(reranker.records)
            result_object = await retrieve(query if arm=='history_concat' else case['message'],
                history=tuple(history) if arm!='history_concat' else (),
                query_mode='HISTORY' if arm!='history_concat' else 'RESOLVED',
                tenant_id='rag-tool-dev', user_scope='eval-user', conversation_id='eval-conversation',
                authorization_fingerprint='isolated-eval-authorized', requirement_signature='pure-rag',
                policy_version='pure-rag-v2:'+case['id']+':'+arm,
                original_user_message=case['message'], **scope,
                policy_values={'query_expansion_count':0,'expansion_query_weight':0.0,
                               'candidate_k':20,'top_k':5,'context_max_tokens':2600,
                               'vector_weight':0.5,'lexical_weight':0.5})
            result = result_object.to_dict(include_text=True)
            wire = json.loads(MCPToolManager._render_for_model(None, ToolResult(
                True, result, 'knowledge_search', authority='knowledge.active_source')))
            row = {'id':case['id'], 'arm':arm, 'query':query, 'original':case['message'],
                   'history':case['history'], 'rewrite_error':error if arm!='history_concat' else None,
                   'rewrite_calls':rewrite_calls if arm in ('standalone_raw','scope_unfiltered') else [],
                   'retrieval':source.records[rs:], 'rerank':reranker.records[rk:],
                   'result':result, 'wire':wire, 'measured_ms':(time.perf_counter()-start)*1000}
            rows.append(row)
            with (output/'pure-cases.jsonl').open('a') as f:
                f.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
        print('PURE',case['id'],error or 'OK',flush=True)
    (output/'pure-cases.jsonl.gz').write_bytes(gzip.compress((output/'pure-cases.jsonl').read_bytes(),mtime=0))
    (output/'pure-completion.json').write_text(json.dumps({'cases':len(cases),'runs':len(rows),
        'api_calls':len(client.calls),'scope':'retrieval only; no business, generation or publication',
        'reranker':reranker.version,'history_owner':'QueryTransformer standalone adapter; not Conversation Agent/memory'},indent=2)+'\n')
