"""First-decision actor and fixed-candidate reviewer probes, not business E2E.

Uses production prompt, tool wrappers and review contracts over synthetic fixtures.
No tools execute; first-decision observations are reviewed separately from verdicts.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.store.memory import InMemoryStore

from application.agent_result import FactRecord, FactSourceKind
from application.capability_registry import CapabilityEffect, CapabilityRisk
from application.orchestration_runtime import AgentContextView
from application.work_item import WorkItem, ControlMode
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture
from evaluation.tau3_tool_binding import bind_environment
from infrastructure.target_domain_outcome import DomainOutcomeReview
from infrastructure.target_framework_agent import TargetFrameworkAgent
from mcp.tool_manager import MCPToolManager


def fixture(case, actor, verifier):
    schema = {"type": "object", "properties": {
        "object_id": {"type": "string"}, "destination": {"type": "string"},
        "item_ids": {"type": "array", "items": {"type": "string"}}},
        "required": ["object_id"], "additionalProperties": False}
    tools = [SimpleNamespace(name=name, openai_schema={"function": {
        "description": description, "parameters": schema}})
        for name, description in case['tools'].items()]
    policy = '\n'.join(f'{name}: {description}' for name, description in case['tools'].items())
    environment = SimpleNamespace(get_tools=lambda: tools, get_policy=lambda: policy,
        tools=SimpleNamespace(tool_type=lambda name: SimpleNamespace(value='write')))
    manager = MCPToolManager('diagnostic-only', model='not-used')
    async def forbidden_call(*args):
        raise AssertionError('No business tool may execute in a first-decision probe')
    registry = bind_environment(environment, manager, forbidden_call)
    item = WorkItem(case['id'], 'retail', case['goal'], ControlMode.DELEGATED,
        ('observed_operation_status',), (), (), (), (), CapabilityEffect.READ, CapabilityRisk.LOW,
        'diagnostic-output', 'environment-evidence:v1', 1, registry.fingerprint, 90, 4,
        allowed_actions=tuple(action.ref for action in registry.actions))
    fact = FactRecord(case['id'], 'fixture.state', json.dumps(case['facts']),
        FactSourceKind.VERIFIED_STATE, 'fixture:' + case['id'], 'synthetic-fixture', 'v1',
        datetime(2026, 9, 9, tzinfo=timezone.utc))
    context = AgentContextView(item, case['goal'], (fact,), (), (), 24000)
    worker = TargetFrameworkAgent(actor, manager, review_model=verifier,
        review_available_tokens=24000, result_store=InMemoryStore(),
        registry=registry, system_prompt=policy)
    exposed = [*(worker._action_tool(action.ref) for action in registry.actions),
               *worker._interaction_tools()]
    return worker, context, exposed, policy


async def run(args):
    cases = json.loads(args.cases.read_text())
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    models = {role: framework_model(policy.profile(role),
        {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url}, max_tokens=4096)
        for role in (ModelRole.WORKER, ModelRole.VERIFIER)}
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = {'scope': __doc__, 'cases': cases,
        'cases_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'profiles': {role.value: policy.profile(role).to_dict() for role in models},
        'max_api_calls': len(cases) * 2, 'business_tools_enabled': False,
        'max_tokens_per_call': 4096, 'timeout_seconds': 90}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for case in cases:
        worker, context, tools, business_policy = fixture(case, models[ModelRole.WORKER], models[ModelRole.VERIFIER])
        messages = [HumanMessage(worker._build_prompt(context))]
        row = {'id': case['id'], 'expected_review': case['expected']}
        for stage in ('actor', 'reviewer'):
            capture = FrameworkCapture(limit=1)
            try:
                async with asyncio.timeout(90):
                    if stage == 'actor':
                        response = await models[ModelRole.WORKER].bind_tools(tools).ainvoke(
                            [SystemMessage(worker._system(context)), *messages],
                            config={'callbacks': [capture]})
                        row['actor_tool_calls'] = response.tool_calls
                        row['actor_text'] = response.text
                        row['actor_forbidden_call'] = any(c['name'] in case['actor_forbidden'] for c in response.tool_calls)
                    else:
                        review = DomainOutcomeReview(models[ModelRole.VERIFIER], callbacks=(capture,),
                            available_tokens=24000, business_policy=business_policy, tools=tools)
                        row['review'] = await review.assess(context=context, messages=messages,
                            kind='PREPARE_ACTION', candidate={'tool': 'prepare_' + case['candidate'],
                            'arguments': case['arguments']})
                        row['review_correct'] = row['review']['accepted'] == case['expected']
            except Exception as exc:
                row[stage + '_error'] = {'type': type(exc).__name__, 'detail': str(exc)}
            row[stage + '_calls'] = capture.calls
        with (args.output / 'results.jsonl').open('a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
        print(case['id'], row.get('actor_tool_calls', row.get('actor_error')),
              row.get('review', row.get('reviewer_error')), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
