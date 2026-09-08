"""Guard plus domain-agent result persistence boundary; no model invocation."""
import asyncio
import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from langchain_core.messages import ToolMessage
from langgraph.store.memory import InMemoryStore
from core.input_security import UntrustedContentGuard
from infrastructure.target_context_compaction import ToolResultPersistence
from infrastructure.target_result_archive import TargetResultArchive
from infrastructure.target_agent_result_adapter import framework_artifact
from mcp.tool_manager import ToolResult


async def replay(rows):
    archive=TargetResultArchive(InMemoryStore());results=[]
    for row in rows:
        for arm,value in row['arms'].items():
            wire=value['wire'];decision=UntrustedContentGuard().analyze(wire)
            if decision.blocked:
                results.append({'case_id':row['case_id'],'arm':arm,'blocked':True,'categories':[str(c) for c in decision.categories]})
                continue
            context=SimpleNamespace(trusted_context={'tenant_id':'mtrag-eval','user_id':'eval','conversation_id':row['case_id']},work_item=SimpleNamespace(owner_agent='general',control=None,work_item_id="weight-"+arm.replace(".","-")))
            # Artifact is for archive identity only; no claim of a retrieved Fact conversion.
            result=ToolResult(success=True,data={'frozen_serialized_view':json.loads(wire)},tool_name='knowledge_search',output_for_model=wire,authority='knowledge.active_source')
            original=ToolMessage(content=wire,tool_call_id='eval-'+arm,artifact=framework_artifact(result))
            async def handler(_request):return original
            update=await ToolResultPersistence(archive).awrap_tool_call(SimpleNamespace(runtime=SimpleNamespace(context=context)),handler)
            message=update.update['messages'][0]
            if update.update.get('archive_failed'):raise ValueError(update.update['tool_observations'][original.tool_call_id]['archive_error'])
            ref=update.update['tool_observations'][original.tool_call_id]['reference']
            stored=await archive.load(context,ref)
            assert stored['content']==wire
            offloaded=message.content!=wire
            if offloaded:assert json.loads(message.content)['result_ref']==ref
            results.append({'case_id':row['case_id'],'arm':arm,'blocked':False,'offloaded':offloaded,'original_recovered':True,'visible_content_sha256':hashlib.sha256(message.content.encode()).hexdigest(),'original_sha256':hashlib.sha256(wire.encode()).hexdigest()})
    return results


async def main():
    source=Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz')
    rows=json.loads(gzip.decompress(source.read_bytes()))
    result={'persistence':await replay(rows)}
    out=Path('artifacts/eval/rag-g4-mtrag-persistence-inline-2026-09-08');out.mkdir(exist_ok=False)
    summary={b:{'views':len(rs),'blocked':sum(r['blocked'] for r in rs),'offloaded':sum(r.get('offloaded',False) for r in rs),'recoverable':sum(r.get('original_recovered',False) for r in rs)} for b,rs in result.items()}
    report={'scope':'guard + domain persistence; fresh content remains inline; whole-context admission is tested separately','api_calls':0,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'summary':summary}
    (out/'cases.json').write_text(json.dumps(result,indent=2)+'\n');(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))

if __name__=='__main__':asyncio.run(main())
