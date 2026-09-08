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


class ReplayTransformer:
    def __init__(self, path):
        self.queries = {}
        for row in map(json.loads, gzip.decompress(Path(path).read_bytes()).splitlines()):
            if row['arm']=='standalone_raw':
                key=(row['original'],tuple(f'{role}: {text}' for role,text in row['history']))
                self.queries[key]=(row['query'],row['rewrite_error'])
    async def standalone(self, query, history=()):
        return self.queries[(query,tuple(history))]


async def run(*, inputs, output, retrieve, client, policy, source, reranker, transformer):
    cases = json.loads(Path(inputs).read_text())
    rows = []
    for case in cases:
        history = [f'{role}: {text}' for role, text in case['history']]
        before = len(client.calls)
        rewritten, error = await transformer.standalone(case['message'], history)
        rewrite_calls = client.calls[before:]
        concat = '\n'.join([*history, 'user: '+case['message']]) if history else case['message']
        for arm, query in [('history_concat', concat), ('standalone_raw', rewritten)]:
            start = time.perf_counter()
            rs, rk = len(source.records), len(reranker.records)
            result_object = await retrieve(query if arm=='history_concat' else case['message'],
                history=tuple(history) if arm=='standalone_raw' else (),
                query_mode='HISTORY' if arm=='standalone_raw' else 'RESOLVED',
                tenant_id='rag-tool-dev', user_scope='eval-user', conversation_id='eval-conversation',
                authorization_fingerprint='isolated-eval-authorized', requirement_signature='pure-rag',
                policy_version='pure-rag-v2:'+case['id']+':'+arm,
                original_user_message=case['message'],
                policy_values={'query_expansion_count':0,'expansion_query_weight':0.0,
                               'candidate_k':20,'top_k':5,'context_max_tokens':2600,
                               'vector_weight':0.5,'lexical_weight':0.5})
            result = result_object.to_dict(include_text=True)
            wire = json.loads(MCPToolManager._render_for_model(None, ToolResult(
                True, result, 'knowledge_search', authority='knowledge.active_source')))
            row = {'id':case['id'], 'arm':arm, 'query':query, 'original':case['message'],
                   'history':case['history'], 'rewrite_error':error if arm=='standalone_raw' else None,
                   'rewrite_calls':rewrite_calls if arm=='standalone_raw' else [],
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
