"""Frozen empty-state planner payload comparison; real compiler, no tools executed.

Uses only the current production provider. Historical outputs are input evidence,
not selectable legacy model runtimes.
Reconstructed bindings preserve captured
values/source refs and unique or ambiguous resolutions; active workstream and
control scenarios are explicitly excluded.
"""
from infrastructure.target_model_context import planning_payload_from_request
import argparse
import asyncio
import copy
from dataclasses import asdict
import gzip
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values
from application.conversation_agent import ConversationAgent
from application.conversation_actions import planning_actions
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.entity_binding import BindingSource, EntityBinding, EntityBindingSet
from core.model_policy import ModelPolicy, ModelRole
from core.framework_models import framework_model
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from evaluation.framework_capture import FrameworkCapture


def unclassify_legacy_references(payload):
    """Explicit counterfactual input migration, never relabel historical results."""
    payload = copy.deepcopy(payload)
    for group in payload['entity_bindings']:
        if group['field_name'] in ('order_id', 'asset_id'):
            sources = {c['source'] for c in group['candidates']}
            textual = {'CURRENT_MESSAGE', 'RECENT_MESSAGE', 'SUMMARY'}
            if sources & textual:
                if not sources <= textual or any(c.get('type_selection') for c in group['candidates']):
                    raise ValueError('mixed or already classified legacy bindings require a fresh capture')
                group['field_name'] = 'reference'
    fields = [g['field_name'] for g in payload['entity_bindings']]
    if len(fields) != len(set(fields)):
        raise ValueError('merged reference groups require a fresh capture')
    payload['schema_version'] = 'conversation-plan-request-v2-references'
    return payload


def compile_captured(key,payload,output):
    if payload['active_workstreams'] or payload['active_work_controls']:
        raise ValueError('only empty-state compilation supported')
    state=ConversationState.empty(tenant_id='plan-replay',user_id='eval',conversation_id=key)
    bindings=[]
    for group in payload['entity_bindings']:
        if group['status'] not in ('UNIQUE', 'AMBIGUOUS'):
            raise ValueError('only unique or ambiguous captured bindings supported')
        for c in group['candidates']:
            bindings.append(EntityBinding.create(group['field_name'],c['value'],source=BindingSource(c['source']),
                source_ref=c['source_ref'],tenant_id='plan-replay',user_id='eval',conversation_id=key,priority=400,
                type_selection=c.get('type_selection')))
    reconstructed=EntityBindingSet(tuple(bindings))
    if reconstructed.as_payload(state) != payload['entity_bindings']:
        raise ValueError('captured binding resolutions cannot be reconstructed faithfully')
    context=SimpleNamespace(entity_bindings=reconstructed,
                            knowledge_filter_contract=payload.get("knowledge_filter_contract"),
                            recent_relevant_turns=tuple(payload['conversation_context'].get('recent_messages',()))
                                + ((payload['conversation_context']['summary'],)
                                   if payload['conversation_context'].get('summary') else ()))
    return ConversationAgent(SimpleNamespace())._validate_and_compile(
        output,TurnObservations(payload['message'],()),state,
        build_default_capability_registry('plan-replay'),context,())


async def run(args):
    raw=gzip.decompress(args.capture.read_bytes())
    rows=[json.loads(line) for line in raw.splitlines()]
    if args.case_ids:
        wanted=set(args.case_ids)
        available={r['case_id'] for r in rows}
        if wanted-available:
            raise ValueError(f'unknown case IDs: {sorted(wanted-available)}')
        rows=[r for r in rows if r['case_id'] in wanted]
    inputs=[(r['case_id'], r['planning_input'] if 'planning_input' in r else
             planning_payload_from_request(r['api_calls'][0]['request'])) for r in rows]
    if args.unclassify_legacy_references:
        inputs = [(key, unclassify_legacy_references(payload)) for key, payload in inputs]
    if any(p['active_workstreams'] or p['active_work_controls'] for _,p in inputs):
        raise ValueError('only captured empty-state cases supported')
    for key,payload in inputs:
        # Validate reconstruction before spending inference calls, regardless of
        # the eventual model decision. This does not supply a model answer.
        compile_captured(key,payload,{'status':'out_of_scope'})
    if any('supported_goals' not in payload for _, payload in inputs):
        raise ValueError('capture lacks capability input; supply a replay artifact with planning_input')
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
    policy=ModelPolicy.from_env(values);profile=policy.profile(ModelRole.INTENT)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'manifest.json').write_text(json.dumps({'scope':__doc__,'cases':len(inputs),
        'source_sha256':hashlib.sha256(raw).hexdigest(),'case_ids':[key for key,_ in inputs],
        'unclassify_legacy_references':args.unclassify_legacy_references,
        'profile':profile.to_dict(),'max_tokens':args.max_tokens,
        'provider_version':AnthropicConversationPlanningProvider.version,
        'sources': {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
            __file__, 'infrastructure/target_conversation_provider.py', 'application/conversation_actions.py',
            'application/conversation_agent.py', 'infrastructure/target_model_context.py', 'core/framework_models.py')},
        'max_api_calls':len(inputs), 'inputs': [{'case_id': key, 'payload': payload,
          'actions': [a.tool() for a in planning_actions(payload)]} for key, payload in inputs]},indent=2)+'\n')
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url:options['base_url']=policy.base_url
    model = framework_model(profile, options, max_tokens=args.max_tokens)
    for key,payload in inputs:
        capture = FrameworkCapture(limit=1)
        provider = AnthropicConversationPlanningProvider({ModelRole.INTENT: model},
            model_profile=profile,synthesis_profile=policy.profile(ModelRole.SYNTHESIS),
            max_tokens=args.max_tokens, callbacks=(capture,))
        result = {'case_id': key, 'planning_input': payload, 'provider_version': provider.version, 'error': None}
        try:
            output = await provider.plan(payload)
            result['output'] = output
            result['proposal'] = asdict(compile_captured(key, payload, output))
        except Exception as error:
            result.update(error=type(error).__name__, error_detail=str(error))
        result['api_calls'] = capture.calls
        with (args.output/'cases.jsonl').open('a') as stream:
            stream.write(json.dumps(result,ensure_ascii=False,default=str)+'\n')
        print(key, result['error'] or result['proposal']['disposition'], flush=True)
    (args.output/'cases.jsonl.gz').write_bytes(gzip.compress((args.output/'cases.jsonl').read_bytes(), mtime=0))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--unclassify-legacy-references',action='store_true',
                   help='Explicitly migrate old free-text entity candidates to untyped references for current-contract replay.')
    p.add_argument('--case-ids',nargs='+',help='Replay only named captured cases; unknown IDs fail before API calls.')
    p.add_argument('--max-tokens',type=int,choices=range(256,8193),metavar='256..8192',default=2048)
    asyncio.run(run(p.parse_args()))


if __name__=='__main__':main()
