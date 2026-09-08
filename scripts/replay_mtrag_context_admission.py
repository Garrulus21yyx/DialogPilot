"""Replay public packed evidence through current domain context owners, no LLM.

Queries and packs are frozen. This is not an Agent query-generation or answer test.
"""
import asyncio,gzip,json,hashlib
from pathlib import Path
from langgraph.store.memory import InMemoryStore
from langchain_core.messages import AIMessage,HumanMessage,ToolMessage
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from types import SimpleNamespace
from application.default_capability_registry import build_default_capability_registry
from application.capability_registry import CapabilityEffect,CapabilityRisk
from application.work_item import WorkItem,ControlMode,ArgumentValue
from application.orchestration_runtime import AgentContextView
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.target_context_compaction import ToolResultPersistence,ContextCompaction
from infrastructure.target_agent_middleware import model_overhead_tokens
from mcp.tool_manager import MCPToolManager,Tool
from scripts.run_mtrag_reranker_pair import at5

async def main():
    source=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    rows=json.loads(gzip.decompress(source.read_bytes()))
    ref=Path('/tmp/dialogpilot-rag-external-lock-20260907/reference.jsonl')
    histories={r['task_id']:r['input'] for line in ref.read_text().splitlines() if (r:=json.loads(line))['task_id'] in {x['case_id'] for x in rows}}
    records=[]
    for row in rows:
        turns=histories[row['case_id']]
        for arm,pack in row['arms'].items():
            query=json.loads(pack['wire'])['query_used']
            item=WorkItem('admission','general',query,ControlMode.DELEGATED,('knowledge_search',),(),(ArgumentValue.create('question',query),),('knowledge.active_source',),(),CapabilityEffect.READ,CapabilityRisk.MEDIUM,'agent-result-v1','customer-service-default:v1',1,'registry:eval',timeout_seconds=60,max_steps=8)
            history=tuple(json.dumps({'role':t['speaker'],'text':t['text']},ensure_ascii=False) for t in turns[:-1])
            context=AgentContextView(item,turns[-1]['text'],(),history,(),2000,{'tenant_id':'tenant-a','user_id':'eval','conversation_id':row['case_id'],'request_id':'admission','invocation_key':'admission'})
            tools=MCPToolManager('unused')
            tools.register(Tool('knowledge_search','Search knowledge and return source evidence.',lambda p,c:None,{'type':'object','properties':{'query':{'type':'string'}},'required':['query']},authority='knowledge.active_source'))
            # Empty responses means an attempted summary fails, never inventing
            # a successful compacted history for this zero-model replay.
            model=FakeListChatModel(responses=[])
            agent=TargetFrameworkAgent(model,tools,review_model=model,review_available_tokens=14200,result_store=InMemoryStore(),registry=build_default_capability_registry('tenant-a'),system_prompt='Answer from source evidence; preserve conditions and uncertainty.')
            error=None;visible=[];count=None
            try:
                overhead=model_overhead_tokens(agent._system(context),agent._tools(context))
                prompt=await agent._prepare_prompt(context,overhead_tokens=overhead)
                pinned=HumanMessage(content=prompt,id='task')
                message=ToolMessage(content=pack['wire'],tool_call_id='search',artifact={'schema':'tool-result-v1','result':{'success':True,'authority':'knowledge.active_source'}})
                async def handler(_):return message
                command=await ToolResultPersistence(agent._archive).awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=context)),handler)
                state={'messages':[pinned,AIMessage(content='',tool_calls=[{'name':'knowledge_search','id':'search','args':{'query':query}}]),*command.update['messages']]}
                owner=ContextCompaction(model,agent._archive,available_tokens=14200,overhead_tokens=overhead,pinned_message=pinned)
                update=await owner.abefore_model(state,SimpleNamespace(context=context))
                final=[m for m in update['messages'] if isinstance(m,(HumanMessage,AIMessage,ToolMessage))] if update else state['messages']
                count=owner.count(final);assert count<=14200
                tool=next(m for m in final if isinstance(m,ToolMessage))
                if tool.content==pack['wire']:visible=pack['packed_ids']
            except Exception as exc:error=type(exc).__name__
            records.append({'case_id':row['case_id'],'arm':arm,'admitted':error is None,'error':error,'context_tokens':count,'packed_count':len(pack['packed_ids']),'fully_inline':visible==pack['packed_ids'],'visible_metrics':at5(visible,set(row['gold']))})
    summary={a:{'fully_inline':sum(r['fully_inline'] for r in records if r['arm']==a),'admission_errors':sum(not r['admitted'] for r in records if r['arm']==a),**{k:sum(r['visible_metrics'][k] for r in records if r['arm']==a)/len(rows) for k in records[0]['visible_metrics']}} for a in rows[0]['arms']}
    out=Path('artifacts/eval/rag-mtrag-context-admission-2026-09-08');out.mkdir(exist_ok=False)
    (out/'report.json').write_text(json.dumps({'scope':__doc__,'api_calls':0,'summary':summary,'records':records,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'history_sha256':hashlib.sha256(ref.read_bytes()).hexdigest()},indent=2)+'\n')
    print(json.dumps(summary,indent=2))

if __name__=='__main__':asyncio.run(main())
