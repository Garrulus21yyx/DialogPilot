"""Capture actual composition provider inputs with a zero-API transport stub."""
import asyncio
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from langchain_core.messages import AIMessage
from application.agent_result import AgentResult, AgentResultStatus, FactRecord, FactSourceKind
from application.result_board import ResultBoardSnapshot
from application.response_assembly import ResponseAssembler
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from core.model_policy import ModelProfile, ModelRole, ReasoningEffort


class Capture:
    async def ainvoke(self,messages,**kwargs):
        self.messages=messages
        return AIMessage(content='CAPTURE_ONLY_NOT_AN_ANSWER')


async def replay(rows):
    profile=ModelProfile('deepseek-v4-flash',ReasoningEffort.NONE,'deepseek')
    records=[]
    for row in rows:
        for arm,v in row['arms'].items():
            view=json.loads(v['wire'])
            # Explicit reconstructed fixture, not a claim of real tool execution.
            data={'status':'OK','evidence_pack':{'query':view['query_used'],'index_manifest_fingerprint':'frozen-eval-fixture','items':[{'chunk_id':cid,'text':e['text'],'title':e['title'],'source_ref':e['source']} for cid,e in zip(v['packed_ids'],view['evidence'],strict=True)]}}
            fact=FactRecord('knowledge-fixture','knowledge.active_source',json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')),FactSourceKind.KNOWLEDGE_ASSERTED,'fixture-source','knowledge_search','eval-reconstructed-v1',datetime(2026,9,8,tzinfo=timezone.utc))
            result=AgentResult('knowledge-fixture','general',AgentResultStatus.SUCCEEDED,'KNOWLEDGE_EVIDENCE_AVAILABLE','target-tool-executor-v1',facts=(fact,))
            board=ResultBoardSnapshot((result,),(fact,),(),(),(),(),True,False)
            capture=Capture()
            provider=AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS:capture},model_profile=profile,synthesis_profile=profile)
            response=await ResponseAssembler(composer=provider)._assemble_candidate(board,current_message=view['query_used'])
            if not response.composer_used:raise ValueError(response.verification_reason)
            payload=json.loads(capture.messages[1].content)
            actual=payload['evidence']['facts'][0]['value']
            assert actual==view
            assert payload['current_message']==view['query_used']
            records.append({'case_id':row['case_id'],'arm':arm,'evidence_count':len(actual['evidence']),'full_view_equal':True,'payload_sha256':hashlib.sha256(capture.messages[1].content.encode()).hexdigest(),'input_characters':len(capture.messages[1].content)})
    return records


async def main():
    source=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    records=await replay(json.loads(gzip.decompress(source.read_bytes())))
    out=Path('artifacts/eval/rag-g4-mtrag-direct-input-2026-09-08');out.mkdir(exist_ok=False)
    (out/'cases.json').write_text(json.dumps(records,indent=2)+'\n')
    report={'scope':'reconstructed knowledge Fact -> ResponseAssembler candidate -> actual compose input; transport stub, no answers','api_calls':0,'views':len(records),'full_views_preserved':sum(r['full_view_equal'] for r in records),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))

if __name__=='__main__':asyncio.run(main())
