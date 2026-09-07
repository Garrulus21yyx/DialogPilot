"""Development exercise of unified chat application with real business and knowledge tools."""
from dataclasses import asdict
from datetime import datetime, timezone
import asyncio
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

from application.chat_contracts import ChatCommand
from application.conversation_agent import ConversationAgent
from application.default_capability_registry import build_default_capability_registry
from application.orchestration_runtime import OrchestrationRuntime
from application.response_assembly import ResponseAssembler
from application.target_chat_application import TargetChatApplication
from application.target_conversation_manager import TargetConversationManager
from application.target_understanding import CascadedTargetUnderstanding, StateBoundTargetUnderstanding
from core.model_policy import ModelRole
from core.framework_models import conversation_models, framework_model
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_tool_execution import TargetToolExecutor
from infrastructure.target_turn_context import TargetTurnContextLoader
from infrastructure.postgres_memory_projection import PostgresMemoryProjectionReader
from infrastructure.postgres_conversation import PostgresConversationTurnStore
from application.conversation_store import ConversationScope, TurnRole, TurnToAppend
from infrastructure.target_chat_adapters import PostgresTargetAdmission, PostgresTargetPublication
from infrastructure.postgres_response_delivery import PostgresResponseDeliveryService
from infrastructure.postgres_target_runtime import PostgresConversationStateStore
from infrastructure.postgres_memory_fact_store import PostgresMemoryFactStore
from memory.conversation_memory import MemoryManager
from mcp.tool_manager import MCPToolManager, Tool
from mcp.customer_operations_tools import customer_operation_tools
from services.customer_operations import CustomerOperationsService, OrderStatus
from services.answer_verifier import AnswerVerifier


class RecordedTools(MCPToolManager):
    async def execute_for_agent(self, name, params, **kwargs):
        result = await super().execute_for_agent(name,params,**kwargs)
        self.captures.append({'name':name,'params':params,'result':asdict(result)})
        return result


async def run_mixed(*, platform, store, client, policy, provider_config, output, handler, case_definitions=None):
    tenant,user='rag-tool-dev','eval-user'
    registry=build_default_capability_registry(tenant)
    business=CustomerOperationsService(platform,tenant_id=tenant)
    snapshots=[]
    for order_id,status in [('DP9301',OrderStatus.SHIPPED),('DP9302',OrderStatus.PAID),('DP9303',OrderStatus.DELIVERED)]:
        snapshots.append(business.upsert_order(order_id=order_id,user_id=user,item_name='模拟耳机',amount_minor=19900,currency='CNY',status=status).to_dict())
    tools=RecordedTools(api_key='unused-transport',model='unused')
    await tools._client.close()
    tools._client=client
    tools.captures=[]
    for tool in customer_operation_tools(business):
        if tool.read_only:
            tools.register(tool)
    from application.knowledge_tool_contract import knowledge_query_schema, knowledge_tool_schema_for_context
    tools.register(Tool(name='knowledge_search',description='查询当前有效政策原文证据',handler=handler,
                        schema=knowledge_query_schema(),schema_factory=knowledge_tool_schema_for_context,
                        schema_factory_version="knowledge-filter-contract-v1",
                        authority='knowledge.active_source',read_only=True))
    capture=FrameworkCapture(limit=client.limit, calls=client.calls)
    agent=ConversationAgent(AnthropicConversationPlanningProvider(conversation_models(policy, provider_config),model_profile=policy.profile(ModelRole.INTENT), synthesis_profile=policy.profile(ModelRole.SYNTHESIS), callbacks=(capture,)))
    verifier=AnswerVerifier(framework_model(policy.profile(ModelRole.VERIFIER), provider_config, max_tokens=4096),model_profile=policy.profile(ModelRole.VERIFIER), callbacks=(capture,))
    cases=[
        ('shipping','请查订单DP9301现在是否发货，再说明已发货后提交改址是否就代表修改成功；不要执行改址。',()),
        ('paid','请查订单DP9302的当前状态，并说明支付尾款是否就代表已经发货。',()),
        ('delivered','请查订单DP9303的状态，再说明签收是否就代表不能申请售后。',()),
        ('reference','请先查它现在的状态，再解释签收是否就代表不能申请售后。',('我说的是订单DP9303。','好的，你还想了解什么？')),
        ('missing','请查订单DP9999当前状态，再说明已发货后的改址规则。',()),
    ]
    if case_definitions is not None:
        cases=[(c["case_id"],c["message"],tuple(c["history"])) for c in case_definitions]
    generation=store.active_generation()
    def context():
        return {'knowledge_filter_contract':store.filter_contract_snapshot(),'cache_scope':registry.bundle_version,'bundle_version':registry.bundle_version,
                'pinned_execution_refs':{'bundle_version':registry.bundle_version,'knowledge_backend_ref':generation.backend_fingerprint,
                                         'corpus_manifest_ref':generation.manifest_hash,'retrieval_policy_ref':registry.bundle_version,
                                         'knowledge_generation_ref':generation.generation_id}}
    (output/'mixed-manifest.json').write_text(json.dumps({'scope':'TargetChatApplication + unified ConversationAgent + real read-only ToolManager/business/knowledge + Redis current context + response assembly/publication; excludes HTTP middleware/durable dispatcher/domain worker planning/cross-session memory',
        'orders':snapshots,'cases':cases,'case_definitions':case_definitions,'planner_profile':policy.profile(ModelRole.INTENT).to_dict()},ensure_ascii=False,indent=2)+'\n')
    with tempfile.TemporaryDirectory(prefix='rag-redis-') as temp:
        socket=Path(temp)/'redis.sock'
        process=subprocess.Popen(['redis-server','--port','0','--unixsocket',str(socket),'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        memory=None
        try:
            for _ in range(100):
                if socket.exists(): break
                if process.poll() is not None: raise RuntimeError('isolated Redis exited')
                await asyncio.sleep(.05)
            if not socket.exists():
                raise RuntimeError('isolated Redis startup timed out')
            memory=MemoryManager(redis_url='unix://'+str(socket),fact_store=PostgresMemoryFactStore(platform),api_key='unused')
            await memory._client.close()
            memory._client=client
            manager=TargetConversationManager(state_store=PostgresConversationStateStore(platform),registry=registry,
                understanding=CascadedTargetUnderstanding(StateBoundTargetUnderstanding(),agent),
                orchestration=OrchestrationRuntime(direct_executor=TargetToolExecutor(tools),domain_workers={}),
                context_provider=TargetTurnContextLoader(PostgresMemoryProjectionReader(platform,memory),tools))
            assembler=ResponseAssembler(agent,knowledge_verifier=verifier,
                                        knowledge_source_validator=store.validate_publication_evidence)
            application=TargetChatApplication(manager=manager,admission=PostgresTargetAdmission(platform),
                publication=PostgresTargetPublication(PostgresResponseDeliveryService(platform,resume_binding_secret=uuid.uuid4().hex)),
                bundle_version=registry.bundle_version,response_assembler=assembler,knowledge_context_factory=context)
            for key,message,history in cases:
                conv='mixed-'+key
                if history:
                    for i, text in enumerate(history):
                        turn_id = 'eval-history-' + uuid.uuid4().hex
                        PostgresConversationTurnStore(platform).append_turn(
                            ConversationScope(tenant, user, conv), TurnToAppend(
                                turn_id, turn_id, TurnRole.INBOUND if i % 2 == 0 else TurnRole.ASSISTANT,
                                text, datetime.now(timezone.utc).isoformat()))
                before_api,before_tools=len(client.calls),len(tools.captures)
                outcome=await application.handle(ChatCommand(message=message,user_id=user,tenant_id=tenant,conv_id=conv,
                    request_id='request-'+key,authorization_fingerprint='isolated-mixed-authorized'))
                row={'case_id':key,'message':message,'history':history,'outcome_type':type(outcome).__name__,
                     'outcome':asdict(outcome),'tools':tools.captures[before_tools:],'api_calls':client.calls[before_api:]}
                with (output/'mixed-cases.jsonl').open('a') as stream:
                    stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
                print('mixed',key,type(outcome).__name__,[c['name'] for c in row['tools']],flush=True)
        finally:
            try:
                if memory is not None:
                    await memory.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
