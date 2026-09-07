"""Actual ConversationAgent planning, followed by local raw/resolved retrieval replay."""
import asyncio
import gzip
import hashlib
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from dotenv import dotenv_values
from application.conversation_agent import ConversationAgent
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.entity_binding import EntityBindingResolver
from application.target_conversation_manager import TargetTurnContext, TargetContextMessage, TargetContextProjectionStatus
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from core.model_policy import ModelPolicy, ModelRole, ModelProfile, ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from evaluation.rag_pipeline.dataset import RagDataset
from evaluation.rag_pipeline.metrics import evaluate_ranked_hits
from evaluation.rag_provider_free import projection, ranked, complete, sha
from evaluation.local_bge_m3_retrieval_eval import bm25_matrix
from mcp.rank_fusion import fuse_rankings
from scripts.run_rag_selected_composition_pair import clean

ROOT = Path('artifacts/eval')
OUT = ROOT / 'rag-resolved-query20-provider-v2-2026-09-07'
MODEL = '/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181'


async def plan(cases):
    values = {k: str(v) for k, v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    profile = ModelProfile(model='deepseek-v4-flash', reasoning=ReasoningEffort.NONE, provider='deepseek')
    assert profile.model == 'deepseek-v4-flash' and profile.reasoning.value == 'none'
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url}, max_tokens=2048)
    rows = []
    for c in cases:
        capture = FrameworkCapture(limit=1)
        provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model}, model_profile=profile, synthesis_profile=profile, max_tokens=2048, callbacks=(capture,))
        state = ConversationState.empty(tenant_id='local-eval', user_id='local-user', conversation_id=c.case_id)
        obs = TurnObservations(c.query, ())
        history = tuple(TargetContextMessage('user' if i % 2 == 0 else 'assistant', text, f'{c.case_id}:{i}', i+1) for i, text in enumerate(c.history))
        context = TargetTurnContext(recent_messages=history, projection_status=TargetContextProjectionStatus.READY, source_watermark=len(history), projection_reason_codes=())
        context = replace(context, entity_bindings=EntityBindingResolver().resolve(obs, state, context))
        proposal = await ConversationAgent(provider).plan(obs, state, DeterministicResolver().resolve(obs, state), build_default_capability_registry('local-eval'), context)
        queries = [json.loads(a.value_json) for cmd in proposal.commands if cmd.tool_id == 'knowledge_search' for a in cmd.arguments if a.name == 'query']
        row = {'case_id': c.case_id, 'raw_query': c.query, 'history': list(c.history), 'proposal': asdict(proposal), 'resolved_queries': queries, 'calls': clean(capture.calls)}
        rows.append(row)
        with (OUT / 'queries.jsonl').open('a') as f:
            f.write(json.dumps(row, ensure_ascii=False, default=lambda v: v.value) + '\n')
        print(c.case_id, proposal.disposition.value, queries, flush=True)
        if capture.calls and capture.calls[-1].get('error_type'):
            raise RuntimeError('provider request failed; stop batch and preserve diagnostic capture')
    (OUT / 'profile.json').write_text(json.dumps(profile.to_dict(), indent=2)+'\n')
    return rows


def evaluate(ds, cases, rows):
    from sentence_transformers import SentenceTransformer
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    _, chunks, texts = projection(ds.documents, ds.select_cases('dev'), 512, 64, 'structure_aware')
    ids = [c.chunk_id for c in chunks]
    byid = {c.chunk_id: c for c in chunks}
    hitmap = {c.chunk_id: {'document_id': c.document_id, 'source_start_char': c.start_char, 'source_end_char': c.end_char} for c in chunks}
    # Missing/multiple query is an explicit unsupported planning result, not a raw fallback.
    tasks = [(i, arm, q) for i, (c, row) in enumerate(zip(cases, rows)) for arm, q in [('raw', c.query), ('resolved', row['resolved_queries'][0] if len(row['resolved_queries']) == 1 else None)] if q]
    queries = [q for _, _, q in tasks]
    key = sha({'model': MODEL, 'input': texts, 'dtype': 'float32', 'normalize': True})
    vectors = np.load(Path('/tmp/dialogpilot-local-parent-cache-20260907') / (key + '.npy'))
    encoder = SentenceTransformer(MODEL, device='cuda', local_files_only=True)
    qvec = encoder.encode(queries, batch_size=8, normalize_embeddings=True, convert_to_numpy=True)
    dense = qvec @ vectors.T
    lexical = bm25_matrix(queries, texts)
    del encoder
    import gc
    gc.collect(); torch.cuda.empty_cache()
    tok = AutoTokenizer.from_pretrained('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3', local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained('/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3', local_files_only=True, torch_dtype=torch.float16).to('cuda').eval()
    results = []
    for j, (i, arm, query) in enumerate(tasks):
        routes = {'dense': ranked(dense[j], ids, 40), 'bm25': tuple(ids[k] for k in sorted(range(len(ids)), key=lambda k: (-lexical[j,k], ids[k])) if lexical[j,k] > 0)[:40]}
        pool = fuse_rankings(routes, weights={'dense': .5, 'bm25': .5}, rrf_k=10, top_k=20)
        scores = []
        for start in range(0, len(pool), 4):
            batch = pool[start:start+4]
            encoded = tok([query]*len(batch), [texts[ids.index(cid)] for cid in batch], padding=True, truncation=False, return_tensors='pt')
            assert encoded['input_ids'].shape[1] <= 8192
            with torch.inference_mode():
                scores.extend(model(**{k:v.to('cuda') for k,v in encoded.items()}).logits.view(-1).float().cpu().tolist())
        final = [cid for _,cid in sorted(zip(scores,pool), key=lambda x:(-x[0],x[1]))]
        results.append({'case_id': cases[i].case_id, 'arm': arm, 'query': query, 'routes': routes, 'pool': pool, 'reranked': final, 'complete20': complete(cases[i], pool, byid), 'complete5': complete(cases[i], final[:5], byid), 'metrics5': evaluate_ranked_hits(cases[i], final, hitmap, top_k=5)})
    (OUT / 'retrieval.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in results))
    summary = {'cases': len(cases), 'api_calls': sum(len(r['calls']) for r in rows), 'resolved_query_count': sum(len(r['resolved_queries']) == 1 for r in rows), 'scope': 'production planner; local retrieval replay; no answer generation', 'source_k':40, 'candidate_k':20, 'dense_weight':.5, 'rrf_k':10, 'arms': {}}
    for arm in ('raw','resolved'):
        rs = [r for r in results if r['arm']==arm]
        summary['arms'][arm] = {'evaluated':len(rs), 'complete20':sum(r['complete20'] for r in rs), 'complete5':sum(r['complete5'] for r in rs), 'mrr5_all20_missing_zero':sum(r['metrics5']['mrr'] for r in rs)/len(cases)}
    (OUT / 'report.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


def main():
    ds = RagDataset.load(ROOT / 'doc2dial-rag-mini-dev-v1', verify_checksum=True)
    lookup = {c.case_id:c for c in ds.select_cases('dev')}
    selection = json.loads((ROOT / 'rag-composition20-selected-2026-09-07/manifest.json').read_text())['case_ids']
    cases = [lookup[cid] for cid in selection]
    if not (OUT / 'queries.jsonl').exists():
        OUT.mkdir(exist_ok=True)
        rows = asyncio.run(plan(cases))
    else:
        rows = [json.loads(l) for l in (OUT/'queries.jsonl').read_text().splitlines()]
        assert [r['case_id'] for r in rows] == selection, 'partial run requires explicit recovery; no automatic API retry'
    evaluate(ds,cases,rows)
    for name in ('queries','retrieval'):
        (OUT/(name+'.jsonl.gz')).write_bytes(gzip.compress((OUT/(name+'.jsonl')).read_bytes(),mtime=0))


if __name__ == '__main__':
    main()
