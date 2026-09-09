"""Runtime directory consistency across planning, tools, ingestion and replay."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import jsonschema
import pytest

from application.sales_channels import filter_contract, filter_contract_fingerprint
from application.knowledge_tool_contract import knowledge_query_schema, knowledge_query_options, knowledge_tool_schema_for_context
from application.conversation_agent import ConversationAgent, planning_output_schema
from application.target_conversation_manager import TargetTurnContext
from application.deterministic_resolution import DeterministicResolver, TurnObservations
from application.default_capability_registry import build_default_capability_registry
from infrastructure.knowledge_filter_config import load_knowledge_filter_contract
from mcp.tool_manager import Tool, MCPToolManager
from tests.test_postgres_knowledge_store import store

A = {'catalog_id':'public-a','sales_channels':{'web':'官网购买','store':'实体门店购买'}}
B = {'catalog_id':'public-b','sales_channels':{'partner_shop':'授权经销商购买'}}


def tool(handler=None):
    async def noop(params, context):
        return params
    return Tool('knowledge_search','检索政策',handler or noop,knowledge_query_schema(),
        schema_factory=knowledge_tool_schema_for_context,schema_factory_version='knowledge-filter-contract-v1')


@pytest.mark.parametrize('catalog', [None,A,B])
def test_same_runtime_schema_in_planner_domain_tool_and_manager(catalog):
    from infrastructure.target_framework_agent import TargetFrameworkAgent
    context={'knowledge_filter_contract':catalog} if catalog else {}
    definition=tool()
    expected=knowledge_query_schema(catalog)
    assert definition.input_schema(context)==expected
    # Use the actual domain tool projection, without calling any model.
    domain=object.__new__(TargetFrameworkAgent)
    projected=domain._atomic_tool(definition,context)
    assert projected.args_schema == expected
    options=planning_output_schema(knowledge_filter_contract=catalog)['properties']['goals']['items']['properties']['knowledge_options']
    assert options['properties'] == {key:value for key,value in expected['properties'].items() if key not in {'query','source_types','regions'}}
    manager=MCPToolManager(api_key='test')
    manager.register(definition)
    assert manager.anthropic_tools_for_agent('general',context=context)[0]['input_schema']==expected
    for channel in (filter_contract(catalog)['sales_channels'] or {None:None}):
        params={'query':'政策'} if channel is None else {'query':'政策','sales_channel':channel}
        result=asyncio.run(manager.execute_for_agent('knowledge_search',params,agent_type='general',context=context))
        assert result.success


@pytest.mark.parametrize('catalog,channel',[(None,'web'),(A,'partner_shop'),(B,'web'),(A,'shipping')])
def test_cross_catalog_selection_rejected_before_handler(catalog,channel):
    calls=[]
    async def handler(params,context):
        calls.append(params)
        return params
    manager=MCPToolManager(api_key='test');manager.register(tool(handler))
    params={'query':'退货条件','sales_channel':channel}
    result=asyncio.run(manager.execute_for_agent('knowledge_search',params,agent_type='general',context={'knowledge_filter_contract':catalog}))
    assert not result.success and result.status=='rejected'
    assert not calls
    with pytest.raises(ValueError):
        knowledge_query_options({'sales_channel':channel},catalog)


def test_host_file_refresh_changes_only_new_snapshot(tmp_path,monkeypatch):
    path=tmp_path/'catalog.json'
    monkeypatch.setenv('KNOWLEDGE_FILTER_CATALOG_FILE',str(path))
    path.write_text(json.dumps(A));first=load_knowledge_filter_contract()
    path.write_text(json.dumps(B));second=load_knowledge_filter_contract()
    assert first==A and second==B
    assert filter_contract_fingerprint(first)!=filter_contract_fingerprint(second)
    assert knowledge_query_schema(first)['properties']['sales_channel']['enum']==['store','web']
    monkeypatch.delenv('KNOWLEDGE_FILTER_CATALOG_FILE')
    assert 'sales_channel' not in knowledge_query_schema(load_knowledge_filter_contract())['properties']


@pytest.mark.parametrize('value', [{}, {'catalog_id':'x','sales_channels':{'global':'all'}},
    {'catalog_id':'x','sales_channels':{'web':''}}, {'catalog_id':'x','sales_channels':[],},
    {'catalog_id':'x','sales_channels':{},'tenant':'invented'}])
def test_malformed_config_is_not_silently_disabled(value):
    with pytest.raises(ValueError):filter_contract(value)


def test_actual_planner_receives_and_compiles_new_configured_channel():
    from tests.test_conversation_agent import Provider, _state
    provider=Provider({'status':'resolved','goals':[{'kind':'refund_policy','resolved_query':'如果经销商购买，退货条件是什么？',
        'knowledge_options':{'sales_channel':'partner_shop'}}]})
    agent=ConversationAgent(provider)
    state=_state();observations=TurnObservations('如果经销商购买，退货条件是什么？')
    proposal=asyncio.run(agent.plan(observations,state,DeterministicResolver().resolve(observations,state),
        build_default_capability_registry('tenant-a'),TargetTurnContext(knowledge_filter_contract=B)))
    assert provider.calls[0]['knowledge_filter_contract']==B
    assert proposal.disposition.value=='RESOLVED'
    assert any(arg.name=='sales_channel' and arg.value=='partner_shop' for arg in proposal.commands[0].arguments)


def test_prepare_pins_host_contract_for_planning_execution_and_checkpoint():
    from tests.test_turn_runtime import _manager, _Executor, _identity
    from infrastructure.langgraph_checkpoint import target_checkpoint_serializer
    manager=_manager(_Executor());seen=[]
    original=manager._understanding
    async def understanding(*args):
        seen.append(args[-1].knowledge_filter_contract)
        return await original(*args)
    manager._understanding=understanding
    prepared=asyncio.run(manager.prepare(_identity(),TurnObservations('订单 DP1234 到哪里了？'),execution_context={'knowledge_filter_contract':A}))
    assert seen==[A]
    assert prepared.context.knowledge_filter_contract==prepared.execution_context['knowledge_filter_contract']==A
    serializer=target_checkpoint_serializer()
    restored=serializer.loads_typed(serializer.dumps_typed(prepared))
    assert restored.context.knowledge_filter_contract==restored.execution_context['knowledge_filter_contract']==A


@pytest.mark.parametrize('endpoint',['add','upload'])
def test_ingestion_accepts_configured_new_channel_and_rejects_unregistered(tmp_path,monkeypatch,endpoint,store):
    import io
    from fastapi import UploadFile,HTTPException
    from api import main
    from tests.test_knowledge_ingestion_api import admin
    catalog=tmp_path/'catalog.json';catalog.write_text(json.dumps(B))
    monkeypatch.setenv('KNOWLEDGE_FILTER_CATALOG_FILE',str(catalog))
    knowledge, _, _ = store
    monkeypatch.setattr(main,"_knowledge_store",knowledge)
    async def invoke(channel):
        value={'title':'经销商政策','content':'退货条件','channel':channel,'scope':'public'}
        if endpoint=='add':
            return await main.add_knowledge(main.BatchDocInput(documents=[main.DocInput(**value)]),admin())
        return await main.upload_knowledge(UploadFile(filename='policy.json',file=io.BytesIO(json.dumps([value]).encode())),admin())
    asyncio.run(invoke('partner_shop'))
    before=knowledge.doc_count()
    assert before > 0
    with pytest.raises(HTTPException) as err:asyncio.run(invoke('web'))
    assert err.value.status_code==422 and knowledge.doc_count()==before


def test_no_knowledge_capability_does_not_offer_knowledge_options():
    schema=planning_output_schema(['order_status'],A)
    assert 'knowledge_options' not in schema['properties']['goals']['items']['properties']


def test_provider_uses_the_context_directory_in_actual_model_schema(monkeypatch):
    import infrastructure.target_conversation_provider as module
    from core.model_policy import ModelProfile
    from tests.framework_structured_stub import StructuredStub
    from langchain_core.messages import AIMessage
    from core.model_policy import ModelRole
    captured=[]
    class Model(StructuredStub):
        def bind_tools(self, tools, **kwargs):
            captured.extend(tools)
            return self
    model = Model(responses=[AIMessage(content='ok')])
    provider=module.AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
        model_profile=ModelProfile('model-a'),synthesis_profile=ModelProfile('model-a'))
    asyncio.run(provider.plan({'message':'经销商购买退货条件','supported_goals':['general_qa'], 'knowledge_filter_contract':B}))
    channels=next(tool for tool in captured if tool['name']=='knowledge_search')['input_schema']['properties']['knowledge_options']['properties']['sales_channel']
    assert channels['enum']==['partner_shop']


def test_configured_null_is_an_error(tmp_path,monkeypatch):
    path=tmp_path/'bad.json';path.write_text('null')
    monkeypatch.setenv('KNOWLEDGE_FILTER_CATALOG_FILE',str(path))
    with pytest.raises(ValueError):load_knowledge_filter_contract()


def test_dynamic_schema_cannot_use_unscoped_tool_cache():
    manager=MCPToolManager(api_key='test')
    with pytest.raises(ValueError):manager.register(replace(tool(),cache_ttl=30))
    with pytest.raises(ValueError):manager.register(replace(tool(),schema_factory_version=''))


def test_new_channel_retrieves_itself_and_global_in_real_postgres(store,postgres_database_url):
    from datetime import datetime,timezone
    from mcp.source_document import SourceDocument
    from tests.test_postgres_knowledge_retriever import _request
    from infrastructure.postgres_knowledge_retriever import PostgresKnowledgeCandidateSource
    from infrastructure.hybrid_retrieval_backend import PostgresHybridBackend
    from infrastructure.retrieval_postgres import RetrievalPoolConfig, RetrievalPostgresPool
    knowledge,_,_=store
    catalog=filter_contract({'catalog_id':'custom','sales_channels':{**A['sales_channels'],**B['sales_channels']}})
    knowledge._filter_contract_factory=lambda:catalog
    knowledge.import_documents(tuple(SourceDocument.create(source_id='channel-'+channel,title='退款规则',content='退款规则适用条款。',
        channel=channel,effective_from=datetime(2025,1,1,tzinfo=timezone.utc)) for channel in ['global','web','partner_shop']))
    generation=knowledge.active_generation()
    pool=RetrievalPostgresPool(RetrievalPoolConfig(postgres_database_url,min_size=1,max_size=3));pool.open()
    try:
        source=PostgresKnowledgeCandidateSource(backend=PostgresHybridBackend(pool),generations=knowledge._generations,pool=pool,embed_query=knowledge.embed_query)
        request=replace(_request(),generation_id=generation.generation_id,manifest_fingerprint=generation.manifest_hash,
            policy=replace(_request().policy,backend_fingerprint=generation.backend_fingerprint,lexical_provider=generation.lexical_ranker,embedding_version=generation.embedding_profile.fingerprint),
            as_of=datetime(2026,1,1,tzinfo=timezone.utc),applicable_channel='partner_shop')
        result=asyncio.run(source.search_variants_async(request,[('raw','退款规则',1.0)],top_k=20))
        assert {row['source_id'] for row in result.candidates}=={'channel-global','channel-partner_shop'}
        # Removing a selectable filter does not rewrite persisted source IDs or
        # revoke public source access; ACL/withdrawal are independent owners.
        knowledge._filter_contract_factory=lambda:filter_contract()
        assert {row.channel for row in knowledge._active_sources()}=={'global','web','partner_shop'}
    finally:pool.close()


def test_postgres_resume_executes_with_original_catalog_after_config_change(postgres_database_url):
    from tests.test_turn_runtime import _manager, _Executor, _CountingManager, _identity
    from tests.test_knowledge_answer_boundary import Verifier
    from application.turn_runtime import TurnRuntime
    from application.response_assembly import ResponseAssembler
    from infrastructure.langgraph_checkpoint import AsyncPostgresCheckpointOwner
    from core.identity import IdentityFactory
    from uuid import uuid4
    seen=[]
    class CrashBeforeExecution(_CountingManager):
        async def execute(self,prepared):
            self.execute_calls+=1
            seen.append(prepared.execution_context['knowledge_filter_contract'])
            if self.execute_calls==1:raise RuntimeError('injected crash before execution')
            return await self.manager.execute(prepared)
    executor=_Executor();manager=CrashBeforeExecution(_manager(executor))
    identity=IdentityFactory().create_invocation(tenant_id='tenant-a',user_id='user-a',conversation_id='catalog-'+uuid4().hex,request_id='request')
    async def run():
        async with AsyncPostgresCheckpointOwner(postgres_database_url,setup=True) as saver:
            runtime=TurnRuntime(manager,ResponseAssembler(knowledge_verifier=Verifier(True)),checkpointer=saver)
            with pytest.raises(RuntimeError,match='injected crash'):
                await runtime.execute(identity,TurnObservations('查询订单 DP1234'),execution_context={'knowledge_filter_contract':A})
        async with AsyncPostgresCheckpointOwner(postgres_database_url) as saver:
            runtime=TurnRuntime(manager,ResponseAssembler(knowledge_verifier=Verifier(True)),checkpointer=saver)
            result=await runtime.execute(identity,TurnObservations('查询订单 DP1234'),execution_context={'knowledge_filter_contract':B})
            assert result.managed.board.results[0].status.value == "SUCCEEDED"
    asyncio.run(run())
    assert seen==[A,A] and manager.prepare_calls==1 and executor.calls==1
