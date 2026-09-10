import asyncio,json
from dataclasses import replace
import pytest
from langgraph.store.memory import InMemoryStore
from infrastructure.target_result_archive import TargetResultArchive,ResultArchiveError,result_pointer
from tests.test_target_framework_agent import _context

@pytest.mark.parametrize('size',[1,3999,4000,4001,10001])
def test_scoped_evidence_pages_reconstruct_exact_text_and_preserve_source(size):
    async def run():
        context=_context();archive=TargetResultArchive(InMemoryStore())
        source={'source_id':'source-one','start_char':50,'end_char':50+size}
        view={'status':'OK','evidence':[{'evidence_id':'E1','title':'Policy','text':('条件否定α\n'*size)[:size],'source':source}]}
        wire=json.dumps(view,ensure_ascii=False);ref=await archive.save(context,{'content':wire})
        pointer=result_pointer(ref,wire);directory=json.loads(pointer)['evidence_directory']
        assert directory[0]['evidence_id']=='E1' and len(directory[0]['preview'])<=100
        assert result_pointer(ref,pointer)==pointer
        chunks=[];offset=0
        while offset is not None:
            page=await archive.read(context,ref,offset,4000,'E1')
            assert page['source']==source and page['offset_basis']=='evidence_text' and len(page['text'])<=4000
            chunks.append(page['text']);offset=page['next_offset']
        assert ''.join(chunks)==view['evidence'][0]['text']
        assert (await archive.read(context,ref,0,4000))['text']==wire[:4000]
        with pytest.raises(ResultArchiveError):await archive.read(context,ref,evidence_id='absent')
        other=replace(context,trusted_context={**context.trusted_context,'user_id':'other'})
        with pytest.raises(ResultArchiveError):await archive.read(other,ref,evidence_id='E1')
    asyncio.run(run())

@pytest.mark.parametrize('view',[{'status':'NO_EVIDENCE','evidence':[]},{'status':'OK','evidence':[{'evidence_id':'E','text':'a','source':{}},{'evidence_id':'E','text':'b','source':{}}]}, {'status':'OK','evidence':[{'evidence_id':'E','text':None,'source':{}}]}])
def test_unsupported_navigation_retains_generic_read(view):
    async def run():
        context=_context();archive=TargetResultArchive(InMemoryStore());wire=json.dumps(view)
        ref=await archive.save(context,{'content':wire})
        assert 'evidence_directory' not in json.loads(result_pointer(ref,wire))
        assert (await archive.read(context,ref))['text']==wire
        with pytest.raises(ResultArchiveError):await archive.read(context,ref,evidence_id='E')
    asyncio.run(run())

def test_existing_96_views_and_raw_knowledge_fact_share_evidence_identity():
    import gzip
    from pathlib import Path
    from langchain_core.messages import ToolMessage
    from langchain_core.messages.utils import count_tokens_approximately
    async def run():
        context=_context();archive=TargetResultArchive(InMemoryStore())
        rows=json.loads(gzip.decompress(Path('artifacts/eval/rag-g4-mtrag-pack32-2026-09-08/cases.json.gz').read_bytes()))
        for row in rows:
            for arm,v in row['arms'].items():
                view=json.loads(v['wire']);ref=await archive.save(context,{'content':v['wire']})
                pointer=result_pointer(ref,v['wire'])
                assert count_tokens_approximately([ToolMessage(content=pointer,tool_call_id='t')])<2840
                assert [x['evidence_id'] for x in json.loads(pointer)['evidence_directory']]==[x['evidence_id'] for x in view['evidence']]
                raw={'status':'OK','evidence_pack':{'query':view['query_used'],'index_manifest_fingerprint':'fixture','items':[{'chunk_id':cid,'text':e['text'],'title':e['title'],'source_ref':e['source']} for cid,e in zip(v['packed_ids'],view['evidence'],strict=True)]}}
                raw_ref=await archive.save(context,{'content':json.dumps(raw)})
                for e in view['evidence']:
                    for key in (ref,raw_ref):
                        parts=[];offset=0
                        while offset is not None:
                            p=await archive.read(context,key,offset,2000,e['evidence_id'])
                            assert p['source']==e['source']
                            parts.append(p['text']);offset=p['next_offset']
                        assert ''.join(parts)==e['text']
    asyncio.run(run())

def test_directory_requests_use_same_enforced_limit_and_schema():
    from infrastructure.target_result_archive import MAX_RESULT_PAGE_CHARS
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    from application.default_capability_registry import build_default_capability_registry
    from tests.test_target_framework_agent import ScriptedToolModel,_manager
    model=ScriptedToolModel(responses=[])
    agent=TargetFrameworkAgent(model,_manager([]),review_model=model,review_available_tokens=14200,result_store=InMemoryStore(),registry=build_default_capability_registry('tenant-a'),system_prompt='Read.')
    tool=next(t for t in agent._tools(_context()) if t.name=='read_tool_result')
    schema=tool.tool_call_schema.model_json_schema()['properties']
    integer_limit = next(option for option in schema['limit']['anyOf'] if option['type'] == 'integer')
    assert integer_limit['maximum']==MAX_RESULT_PAGE_CHARS
    assert integer_limit['minimum']==1 and schema['offset']['minimum']==0
    assert schema['limit']['default'] is None
    async def run():
        archive=TargetResultArchive(InMemoryStore());context=_context()
        for n in (1,1999,2000,2001,3999,4000,4001,9999):
            text='x'*n;wire=json.dumps({'status':'OK','evidence':[{'evidence_id':'E','text':text,'source':{}}]})
            ref=await archive.save(context,{'content':wire})
            args=json.loads(result_pointer(ref,wire))['evidence_directory'][0]['read_tool_result']
            assert args['limit']==min(n,MAX_RESULT_PAGE_CHARS)
            page=await archive.read(context,**args)
            assert page['text']==text[:MAX_RESULT_PAGE_CHARS]
            assert page['next_offset']==(MAX_RESULT_PAGE_CHARS if n>MAX_RESULT_PAGE_CHARS else None)
    asyncio.run(run())
