"""Replay captured composition inputs only; no planning, retrieval, or business execution."""
import argparse
import asyncio
import copy
import gzip
import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path

from anthropic import AsyncAnthropic
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelRole
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from scripts.run_rag_tool_calibration import CaptureClient
from application.composition_output import render_composition, prepare_composition_payload
from application.response_assembly import AllowedClaim, ResponseAssembler
from services.answer_verifier import AnswerVerifier


def separate_field_guidance(payload):
    """Evaluation-only layout change; caller retains original evidence to verify."""
    model_input = copy.deepcopy(payload)
    for claim in model_input['allowed_claims']:
        if claim['kind'] == 'FACT' and isinstance(claim['value'], dict) and 'field_semantics' in claim['value']:
            claim['interpretation_guidance'] = claim['value'].pop('field_semantics')
    return model_input


async def run(args):
    if args.separate_field_guidance:
        raise ValueError('legacy field-guidance experiment: reproduce at commit 4a032e3; current supports bind the original claim content')
    raw = gzip.decompress(args.capture.read_bytes()) if args.capture.suffix == '.gz' else args.capture.read_bytes()
    inputs = []
    for line in raw.splitlines():
        row = json.loads(line)
        for call in row['api_calls']:
            request = call['request']
            if not str(request.get('system', '')).startswith('Compose one concise'):
                continue
            payload = json.loads(request['messages'][0]['content'])
            if args.planner_context:
                planner_calls = [c for c in row['api_calls']
                                 if str(c['request'].get('system', '')).startswith('You plan customer-service turns.')]
                if len(planner_calls) != 1:
                    raise ValueError('one captured planner context required for context replay')
                planner_input = json.loads(planner_calls[0]['request']['messages'][0]['content'])
                payload['conversation_context'] = copy.deepcopy(planner_input['conversation_context'])
            # Explicit migration of frozen v1 evaluation inputs to the new owner
            # contract. Runtime producers already supply KNOWLEDGE_FACT.
            if payload['schema_version'] == 'conversation-compose-request-v1':
                for claim in payload['allowed_claims']:
                    value=claim['value']
                    if (claim['kind']=='FACT' and isinstance(value,dict)
                            and value.get('status')=='OK' and isinstance(value.get('evidence'),list)):
                        claim['kind']='KNOWLEDGE_FACT'
                payload['schema_version']='conversation-compose-request-v2-segments'
            if args.business_view_v2:
                from services.customer_operation_views import order_read_view, refund_eligibility_read_view
                for claim in payload['allowed_claims']:
                    if claim['kind'] != 'FACT':
                        continue
                    refs=claim['source_refs']
                    if len(refs)!=1:
                        raise ValueError('business view replay requires one captured tool source')
                    if refs[0].endswith(':order_lookup'):
                        claim['value']=order_read_view(claim['value'])
                    elif refs[0].endswith(':refund_eligibility_check'):
                        claim['value']=refund_eligibility_read_view(claim['value'])
            inputs.append((row['case_id'], prepare_composition_payload(payload)))
    if not inputs:
        raise ValueError('no captured composition inputs')
    if args.case_ids:
        requested=set(args.case_ids)
        if requested-{key for key,_ in inputs}:
            raise ValueError('selected case has no captured composition input')
        inputs=[(key,payload) for key,payload in inputs if key in requested]
    args.output.mkdir(parents=True, exist_ok=False)
    values = {k:str(v) for k,v in dotenv_values('.env').items() if v is not None}
    values.update(os.environ)
    policy = ModelPolicy.from_env(values)
    options = dict(api_key=values['ANTHROPIC_API_KEY'], max_retries=0, timeout=60)
    if policy.base_url:
        options['base_url'] = policy.base_url
    manifest = {'scope':__doc__, 'cases':len(inputs),'source_sha256':hashlib.sha256(raw).hexdigest(),
                'case_ids':[key for key,_ in inputs],
                'business_view_v2':args.business_view_v2,
                'separate_field_guidance':args.separate_field_guidance,
                'planner_context':args.planner_context,
                'synthesis_profile':policy.profile(ModelRole.SYNTHESIS).to_dict(),
                'verify':args.verify,'max_api_calls':len(inputs)*(2 if args.verify else 1),
                'input_migration':'v1 FACT evidence views become KNOWLEDGE_FACT; request schema v3 with captured content-bound support catalog',
                'limitation':'component replay; no live source revalidation, business execution or publication'}
    (args.output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    async with AsyncAnthropic(**options) as transport:
        client = CaptureClient(transport,limit=len(inputs)*(2 if args.verify else 1))
        verifier = AnswerVerifier(client=client,model_profile=policy.profile(ModelRole.VERIFIER)) if args.verify else None
        provider = AnthropicConversationPlanningProvider(client,model_profile=policy.profile(ModelRole.INTENT),
                                                        synthesis_profile=policy.profile(ModelRole.SYNTHESIS))
        for key,payload in inputs:
            before = len(client.calls)
            try:
                value = await provider.compose(payload)
                claims=tuple(AllowedClaim(c['claim_id'],c['kind'],c['value'],tuple(c['source_refs'])) for c in payload['allowed_claims'])
                text,used=render_composition(value,claims)
                text,used=ResponseAssembler.prepare_composed_response(text,used,claims,payload['current_message'],payload['work_item_outcomes'],
                    conversation_context=payload.get('conversation_context'))
                result = {'output':value,'rendered':text,'used_claim_ids':used,'error':None}
                if verifier:
                    packs=[c.value for c in claims if c.kind=='KNOWLEDGE_FACT']
                    verdict=await verifier.verify(payload['current_message'],text,
                        context=json.dumps({'facts':[c.value for c in claims if c.kind in ('FACT', 'CONTROLLED_REFUND_FACT')],
                                            'receipts':[c.value for c in claims if c.kind=='RECEIPT'],
                                            **({'user_context': payload['conversation_context']} if payload.get('conversation_context') is not None else {})},ensure_ascii=False),
                        knowledge_evidence={'packs':packs,'allowed_evidence_ids':sorted({e['evidence_id'] for p in packs for e in p['evidence']})},
                        agent_outcomes=[{'status':o['status'],'reason':o['reason_code']} for o in payload['work_item_outcomes']])
                    result['verdict']={**asdict(verdict),'status':verdict.status.value,'reason_code':verdict.reason_code.value,'publishable':verdict.publishable}
            except Exception as error:
                result = {'output':None,'error':type(error).__name__}
            row = {'case_id':key,'input':payload,**result,'api_calls':client.calls[before:]}
            with (args.output/'cases.jsonl').open('a') as stream:
                stream.write(json.dumps(row,ensure_ascii=False,default=str)+'\n')
            print(key,result['error'] or 'structured output',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--verify',action='store_true',help='Check rendered answers against captured evidence; no live source validation.')
    parser.add_argument('--case-ids',nargs='+',help='Select captured composition cases before spending API calls.')
    parser.add_argument('--business-view-v2',action='store_true',help='Explicitly project captured v1 order/eligibility values through current business read owners.')
    parser.add_argument('--separate-field-guidance',action='store_true',
                        help='Evaluation-only metadata layout; verifier still receives original facts.')
    parser.add_argument('--planner-context',action='store_true',
                        help='Evaluation-only: copy captured planner context into composition and verifier user context.')
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
