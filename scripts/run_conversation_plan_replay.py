"""Frozen empty-state planner payload comparison; real compiler, no tools executed.

Native mode uses the production provider; text/structured are legacy evaluation modes.
Reconstructed bindings preserve captured
values/source refs and unique or ambiguous resolutions; active workstream and
control scenarios are explicitly excluded.
"""
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

from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from application.conversation_agent import ConversationAgent, ConversationProviderOutputError, planning_goal_descriptions, planning_output_schema
from application.conversation_state import ConversationState
from application.default_capability_registry import build_default_capability_registry
from application.deterministic_resolution import TurnObservations
from application.entity_binding import BindingSource, EntityBinding, EntityBindingSet
from core.model_policy import ModelPolicy, ModelRole, ReasoningEffort
from core.provider_context_budget import DEFAULT_PROVIDER_CONTEXT_BUDGET
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from evaluation.legacy_conversation_planning import LegacyTextPlanningProvider
from scripts.run_rag_tool_calibration import CaptureClient


def output_schema(payload):
    from application.knowledge_tool_contract import knowledge_query_options_schema
    text={'type':'string','minLength':1}
    goal={'type':'object','additionalProperties':False,'required':['goal_id','kind'],'properties':{
        'goal_id':text,'kind':{'type':'string','enum':payload['supported_goals']},
        **{key:text for key in ('order_id','order_id_source_ref','asset_id','asset_id_source_ref','new_address','revises_control_id')},
        'resolved_query':{**text,'maxLength':4000},
        'depends_on':{'type':'array','items':text},
        'knowledge_options':knowledge_query_options_schema(payload.get("knowledge_filter_contract")),
    }}
    return {'type':'object','additionalProperties':False,'required':['status'],'properties':{
        'status':{'type':'string','enum':['resolved','insufficient_context','out_of_scope']},
        'goals':{'type':'array','minItems':1,'maxItems':4,'items':goal},
        'missing_fields':{'type':'array','minItems':1,'items':{'type':'string','enum':payload['missing_fields_schema']}},
    }}


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
                            recent_relevant_turns=tuple(payload['conversation_context'].get('recent_messages',()))
                                + ((payload['conversation_context']['summary'],)
                                   if payload['conversation_context'].get('summary') else ()))
    return ConversationAgent(SimpleNamespace())._validate_and_compile(
        output,TurnObservations(payload['message'],()),state,
        build_default_capability_registry('plan-replay'),context)


async def run(args):
    raw=gzip.decompress(args.capture.read_bytes())
    rows=[json.loads(line) for line in raw.splitlines()]
    if args.case_ids:
        wanted=set(args.case_ids)
        available={r['case_id'] for r in rows}
        if wanted-available:
            raise ValueError(f'unknown case IDs: {sorted(wanted-available)}')
        rows=[r for r in rows if r['case_id'] in wanted]
    inputs=[(r['case_id'],json.loads(r['api_calls'][0]['request']['messages'][0]['content'])) for r in rows]
    if args.unclassify_legacy_references:
        inputs = [(key, unclassify_legacy_references(payload)) for key, payload in inputs]
    if args.current_goal_descriptions:
        for _, payload in inputs:
            payload['goal_descriptions']=planning_goal_descriptions()
    if any(p['active_workstreams'] or p['active_work_controls'] for _,p in inputs):
        raise ValueError('only captured empty-state cases supported')
    for key,payload in inputs:
        # Validate reconstruction before spending inference calls, regardless of
        # the eventual model decision. This does not supply a model answer.
        compile_captured(key,payload,{'status':'out_of_scope'})
    if 'structured_native' in args.modes and not args.current_output_schema:
        raise ValueError('production native planning requires --current-output-schema')
    schema_for = (lambda payload: planning_output_schema()) if args.current_output_schema else output_schema
    system_override = args.system_prompt.read_text() if args.system_prompt else None
    if args.current_output_schema:
        from jsonschema import Draft202012Validator
        wire_validator = Draft202012Validator(planning_output_schema())
    values={k:str(v) for k,v in dotenv_values('.env').items() if v is not None};values.update(os.environ)
    policy=ModelPolicy.from_env(values);profile=policy.profile(ModelRole.INTENT)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'manifest.json').write_text(json.dumps({'scope':__doc__,'cases':len(inputs),
        'source_sha256':hashlib.sha256(raw).hexdigest(),'case_ids':[key for key,_ in inputs],
        'unclassify_legacy_references':args.unclassify_legacy_references,
        'current_goal_descriptions':args.current_goal_descriptions,'profile':profile.to_dict(),'max_tokens':args.max_tokens,
        'variants':args.modes,'max_api_calls':len(args.modes)*len(inputs),
        'current_output_schema':args.current_output_schema,'structured_schema':schema_for(inputs[0][1]),
        'system_override_sha256':hashlib.sha256(system_override.encode()).hexdigest() if system_override is not None else None},indent=2)+'\n')
    if system_override is not None:
        (args.output/'system-prompt.txt').write_text(system_override)
    options=dict(api_key=values['ANTHROPIC_API_KEY'],max_retries=0,timeout=60)
    if policy.base_url:options['base_url']=policy.base_url
    async with AsyncAnthropic(**options) as transport:
        client=CaptureClient(transport,limit=len(args.modes)*len(inputs))
        for key,payload in inputs:
            for mode in args.modes:
                class Messages:
                    async def create(self,**request):
                        if system_override is not None:
                            request['system'] = system_override
                        if mode == 'structured':
                            request['tools']=[{'name':'submit_turn_plan','description':'Submit the customer-service turn plan.',
                                               'input_schema':schema_for(payload)}]
                            request['tool_choice']={'type':'tool','name':'submit_turn_plan'}
                        DEFAULT_PROVIDER_CONTEXT_BUDGET.validate(profile,ModelRole.INTENT,request)
                        response=await client.messages.create(**request)
                        if mode == 'structured':
                            blocks=[b for b in response.content if b.type=='tool_use']
                            if response.stop_reason!='tool_use' or len(blocks)!=1 or blocks[0].name!='submit_turn_plan':
                                raise ConversationProviderOutputError('incomplete structured plan')
                            return SimpleNamespace(content=[SimpleNamespace(type='text',text=json.dumps(blocks[0].input))])
                        return response
                provider_type = AnthropicConversationPlanningProvider if mode == 'structured_native' else LegacyTextPlanningProvider
                provider=provider_type(SimpleNamespace(messages=Messages()),
                    model_profile=profile,synthesis_profile=policy.profile(ModelRole.SYNTHESIS),max_tokens=args.max_tokens)
                before=len(client.calls);output=None;result={'case_id':key,'mode':mode,'provider_version':provider.version,'error':None}
                try:
                    output=await provider.plan(payload)
                    if args.current_output_schema:
                        # Separate wire compliance from the existing compiler's
                        # semantic result; a compiled plan is not schema proof.
                        result['wire_schema_valid'] = wire_validator.is_valid(output)
                    proposal=compile_captured(key,payload,output)
                    result.update(output=output,proposal=asdict(proposal))
                except Exception as error:
                    result.update(error=type(error).__name__,output=output)
                result['api_calls']=client.calls[before:]
                if args.current_output_schema:
                    result['wire_schema_valid'] = None
                    result['wire_validation_status'] = 'no_complete_output'
                    if output is not None:
                        result['wire_schema_valid'] = wire_validator.is_valid(output)
                    elif result['api_calls']:
                        response = result['api_calls'][-1].get('response', {})
                        blocks = [b for b in response.get('content', []) if b.get('type') == 'tool_use']
                        if (response.get('stop_reason') == 'tool_use' and len(blocks) == 1
                                and blocks[0].get('name') == 'submit_turn_plan'):
                            result['wire_schema_valid'] = wire_validator.is_valid(blocks[0].get('input'))
                    if result['wire_schema_valid'] is not None:
                        result['wire_validation_status'] = 'valid' if result['wire_schema_valid'] else 'invalid_shape'
                with (args.output/'cases.jsonl').open('a') as stream:stream.write(json.dumps(result,ensure_ascii=False,default=str)+'\n')
                print(key,mode,result['error'] or result['proposal']['disposition'],flush=True)
                output=None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--current-goal-descriptions',action='store_true')
    p.add_argument('--unclassify-legacy-references',action='store_true',
                   help='Explicitly migrate old free-text entity candidates to untyped references for current-contract replay.')
    p.add_argument('--system-prompt',type=Path,help='Explicit evaluation-only instruction override, copied into output artifacts.')
    p.add_argument('--current-output-schema',action='store_true',
                   help='Use the application-owned output shape; historical evaluation schema remains the default.')
    p.add_argument('--case-ids',nargs='+',help='Replay only named captured cases; unknown IDs fail before API calls.')
    p.add_argument('--max-tokens',type=int,choices=range(256,8193),metavar='256..8192',default=2048)
    p.add_argument('--modes',nargs='+',choices=('text','structured','structured_native'),default=['text','structured'])
    asyncio.run(run(p.parse_args()))


if __name__=='__main__':main()
