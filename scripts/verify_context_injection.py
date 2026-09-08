"""Bounded production context verification; synthetic domain tools never write."""
import asyncio
import copy
import gzip
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import dotenv_values
from langgraph.store.memory import InMemoryStore
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.default_capability_registry import build_default_capability_registry
from application.work_item import WorkItem, ControlMode, ArgumentValue
from application.orchestration_runtime import AgentContextView
from infrastructure.target_framework_agent import TargetFrameworkAgent
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from infrastructure.target_model_context import planning_payload_from_request
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort
from evaluation.framework_capture import FrameworkCapture
from mcp.tool_manager import MCPToolManager, Tool
from scripts.run_rag_selected_composition_pair import clean

ROOT = Path(os.environ.get('CONTEXT_VERIFICATION_OUTPUT', 'artifacts/eval/context-injection-convergence-2026-09-08'))


def fixture_evidence(query, text):
    from application.knowledge_tool_contract import evidence_items
    data = {'status': 'OK', 'evidence_pack': {'query': query, 'index_manifest_fingerprint': 'synthetic-context-test',
        'items': [{'chunk_id': 'fixture', 'text': text, 'title': 'Synthetic store policy',
                   'source_ref': {'source_id': 'fixture', 'source_revision': 'v1', 'scope': 'public',
                                  'start_char': 0, 'end_char': len(text),
                                  'checksum': hashlib.sha256(text.encode()).hexdigest()}}]}}
    evidence_items(data)
    return data


async def main():
    ROOT.mkdir(parents=True, exist_ok=False)
    previous = json.loads(Path('artifacts/eval/rag-current-turn-pair6-2026-09-08/manifest.json').read_text())
    cases = copy.deepcopy(previous['cases'])
    for name, message, expected in [
        ('fresh-topic', '先不谈退货，耳机充电盒的保修期限是多久？', 'Charging-case warranty duration only, not return eligibility.'),
        ('fresh-short-reply', '没有质量问题，只是拆封试听过。', 'Opened/used earphone non-quality return policy, not a quality defect or write action.'),
    ]:
        payload = copy.deepcopy(cases[3]['payload'])
        payload['message'] = message
        payload['conversation_context'] = {'summary': None, 'recent_messages': [
            {'role': 'user', 'content': '耳机已经拆封，想了解退货规则。'},
            {'role': 'assistant', 'content': '是质量问题还是其他原因？'}]}
        cases.append({'id': name, 'payload': payload, 'expected': expected, 'scope': 'new synthetic wording, not held-out corpus'})
    domains = [
        {'id': 'domain-warranty', 'objective': '查询耳机充电盒的保修期限，仅解释政策。',
         'source': '查一下耳机充电盒保修多久，另外取消订单DP1234。',
         'evidence': '耳机充电盒的保修期限为购买之日起12个月。',
         'expected': 'Retrieve charging-case warranty and report 12 months; do not cancel order or claim cancellation.'},
        {'id': 'domain-return', 'objective': '查询已拆封试听但没有质量问题的耳机退货条件，仅解释政策，不申请退货。',
         'source': '耳机拆封试听过但没质量问题，我想了解退货规则；顺便查DP1234物流。',
         'evidence': '已拆封试听的耳机，如果没有质量问题，不适用本店七天无理由退货。',
         'expected': 'Preserve opened/tried and no defect, answer fixture policy; do not take over logistics or apply refund.'},
    ]
    if os.environ.get('CONTEXT_VERIFICATION_DOMAINS_ONLY') == '1':
        cases = []
    for case in domains:
        fixture_evidence(case['objective'], case['evidence'])
    files = ['infrastructure/target_model_context.py', 'infrastructure/target_conversation_provider.py',
             'infrastructure/target_framework_agent.py', 'core/structured_model.py']
    manifest = {'gap': 'R01', 'budget': len(cases) + 8, 'planner_calls': len(cases), 'domain_call_cap_each': 4,
                'model': 'deepseek-v4-flash', 'reasoning': 'none', 'retries': 0,
                'cases': cases, 'domains': domains, 'scope': __doc__,
                'criteria': 'All original failures and new cases assessed for topic/negation/reference/task scope; no semantic reruns. Cache usage reported separately, not guaranteed hit rate.',
                'source_sha256': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files}}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = ModelProfile('deepseek-v4-flash', ReasoningEffort.NONE, 'deepseek')
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url}, max_tokens=800)
    records = []
    class DurableCapture(FrameworkCapture):
        def __init__(self, case_id, **kwargs):
            super().__init__(**kwargs)
            self.path = ROOT / (case_id + '-calls.json')
        def on_llm_end(self, response, **kwargs):
            super().on_llm_end(response, **kwargs)
            self.path.write_text(json.dumps(clean(self.calls), ensure_ascii=False, indent=2, default=str))
        def on_llm_error(self, error, **kwargs):
            super().on_llm_error(error, **kwargs)
            self.path.write_text(json.dumps(clean(self.calls), ensure_ascii=False, indent=2, default=str))
    def save(record):
        records.append(clean(record))
        (ROOT / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str)+'\n')
        print(json.dumps({k:v for k,v in record.items() if k not in {'calls', 'result'}}, ensure_ascii=False), flush=True)
    for case in cases:
        capture = DurableCapture(case["id"], limit=1)
        provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model}, model_profile=profile,
                    synthesis_profile=profile, callbacks=(capture,))
        try:
            plan = await provider.plan(case['payload'])
            assert planning_payload_from_request(capture.calls[0]['request']) == case['payload']
            record = {'id': case['id'], 'plan': plan}
        except Exception as exc:
            record = {'id': case['id'], 'error_type': type(exc).__name__}
        save({**record, 'calls': capture.calls})
    for case in domains:
        capture = DurableCapture(case["id"], limit=4)
        manager = MCPToolManager('unused-fixture-key', model='unused-fixture-model')
        tool_calls = []
        async def lookup(params, context):
            tool_calls.append(params)
            return fixture_evidence(params['query'], case['evidence'])
        manager.register(Tool('knowledge_search', 'Search product policy documentation.', lookup,
            {'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query'], 'additionalProperties': False},
            authority='knowledge.active_source'))
        item = WorkItem(case['id'], 'general', case['objective'], ControlMode.DELEGATED, ('knowledge_search',), (),
            (ArgumentValue.create('question', case['objective']),), ('knowledge.active_source',), (),
            CapabilityEffect.READ, CapabilityRisk.MEDIUM, 'agent-result-v1', 'customer-service-default:v1',
            1, 'registry:context-test', timeout_seconds=60, max_steps=3)
        context = AgentContextView(item, case['source'], (), (), (), 2000,
            {'tenant_id': 'tenant-a', 'user_id': 'eval', 'conversation_id': case['id'], 'request_id': case['id'], 'invocation_key': case['id']})
        agent = TargetFrameworkAgent(model, manager, review_model=model, review_available_tokens=14200,
            result_store=InMemoryStore(), registry=build_default_capability_registry('tenant-a'),
            system_prompt='Explain product policies from retrieved evidence.', callbacks=(capture,))
        try:
            result = await agent(context)
            record = {'id': case['id'], 'status': result.status.value, 'answer': result.candidate_response, 'result': asdict(result)}
        except Exception as exc:
            record = {'id': case['id'], 'error_type': type(exc).__name__}
        save({**record, 'tool_calls': tool_calls, 'calls': capture.calls})
    (ROOT / 'results.json.gz').write_bytes(gzip.compress((ROOT / 'results.json').read_bytes(), mtime=0))


if __name__ == '__main__':
    asyncio.run(main())
