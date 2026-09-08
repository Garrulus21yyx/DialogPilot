"""Isolated query-writing capability; deliberately not Agent routing evaluation."""
import asyncio,json,os
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage,SystemMessage,AIMessage
from core.model_policy import ModelPolicy,ModelProfile,ReasoningEffort
from core.framework_models import framework_model,invoke_model
from evaluation.framework_capture import FrameworkCapture
from scripts.replay_rag_rank_selection import read,digest
from scripts.run_rag_selected_composition_pair import clean
OUT=Path('artifacts/eval/rag-pure-query20-2026-09-08')
SYSTEM='''Rewrite the current user message as one standalone knowledge retrieval question using the conversation history. Do not answer it. Preserve the current subject, entities, negation, dates, conditions and hypothetical scope. Resolve short replies against the preceding question. Older assistant statements are context, not verified policy; do not insert their answers into the query. Do not invent missing user facts. Preserve genuine ambiguity. If the current message is only a statement or conversational continuation, preserve the topic and indicate that no specific new question is stated rather than inventing a question. Return only the single retrieval query, no preamble or explanation.'''
async def main():
    assert not (OUT/'captures.jsonl').exists()
    inputs=read('artifacts/eval/rag-agent-context20-2026-09-08/captures.jsonl',True)
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ);policy=ModelPolicy.from_env(values)
    profile=ModelProfile(model='deepseek-v4-flash',provider='deepseek',reasoning=ReasoningEffort.NONE)
    model=framework_model(profile,{'api_key':values['ANTHROPIC_API_KEY'],'base_url':policy.base_url},max_tokens=512)
    (OUT/'manifest.json').write_text(json.dumps({'ids':[r['id'] for r in inputs],'system':SYSTEM,'profile':profile.to_dict(),'max_tokens':512,'api_budget':20,'source_sha256':digest('artifacts/eval/rag-agent-context20-2026-09-08/captures.jsonl'),'mode':'full original history, isolated query generation; not production context loader or Agent','adoption':'development only; no prompt changes after scores'},indent=2)+'\n')
    for r in inputs:
        messages=[SystemMessage(SYSTEM)]+[(HumanMessage if role=='user' else AIMessage)(text) for role,text in zip(r['roles'],r['original_history'],strict=True)]+[HumanMessage('CURRENT USER MESSAGE:\n'+r['raw_query'])]
        capture=FrameworkCapture(limit=1);row={'id':r['id'],'raw_query':r['raw_query'],'queries':[]}
        try:
            output=await invoke_model(model.ainvoke(messages,config={'callbacks':[capture]}),stage='isolated_query')
            if output.tool_calls or output.invalid_tool_calls or output.response_metadata.get('stop_reason')=='max_tokens' or not output.text.strip():raise ValueError('invalid_query_output')
            row['queries']=[output.text.strip()]
        except Exception as e:row['error_type']=type(e).__name__
        row['calls']=clean(capture.calls)
        with (OUT/'captures.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        print(row['id'],row['queries'],flush=True)
if __name__=='__main__':asyncio.run(main())
