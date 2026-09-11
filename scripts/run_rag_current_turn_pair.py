"""Frozen planning inputs: old/new owner adapter, Flash only, no retrieval or writes."""
from infrastructure.target_model_context import planning_payload_from_request
import asyncio, copy, gzip, hashlib, json, os, subprocess, types
from pathlib import Path
from dotenv import dotenv_values
from core.model_policy import ModelPolicy, ModelProfile, ModelRole, ReasoningEffort
from core.framework_models import framework_model
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from scripts.run_rag_selected_composition_pair import clean

ROOT = Path('artifacts/eval/rag-current-turn-pair6-2026-09-08')
async def main():
    ROOT.mkdir(parents=True, exist_ok=False)
    source = Path('artifacts/eval/rag-final-mtrag2-v3-2026-09-08/full-cases.jsonl.gz')
    rows = [json.loads(x) for x in gzip.decompress(source.read_bytes()).decode().splitlines()]
    cases = [{'id': 'mtrag-'+str(i), 'payload': planning_payload_from_request(r['api_calls'][0]['request']),
              'expected': exp, 'scope': 'exposed public dev witness'} for i, (r, exp) in enumerate(zip(rows, [
                  'Preserve weak understanding metrics; definition retrieval or term-specific clarification, not repeat alert examples.',
                  'Improve the Discovery query using prior product context.'], strict=True))]
    ecommerce = [
        ('topic', [('user', '怎么修改配送地址？'), ('assistant', '需要确认订单状态和新的配送地址。')],
         '先不改地址，我想了解耳机拆封后的退货规则。', 'Only retrieve opened-earphone return rules; no address change or order action.'),
        ('negation', [('user', '耳机拆封了，我想了解能否退货。'), ('assistant', '是因为质量问题吗？')],
         '不是，音质没问题。', 'Retrieve opened-earphone non-quality return conditions; preserve negative answer; no invented eligibility.'),
        ('ellipsis', [('user', '我想了解耳机的保修期限。'), ('assistant', '你问的是耳机本体还是充电盒？')],
         '充电盒。', 'Retrieve charging-case warranty duration using prior question.'),
        ('hypothetical', [('user', '我已经签收了耳机。'), ('assistant', '你可以说明遇到了什么问题。')],
         '假如收到后七天内拆封了，但没有质量问题，通常还能退吗？只是问规则，不申请。',
         'Retrieve hypothetical opened-earphone non-quality return within seven days; policy only, no refund action or asserted actual opening.')]
    for name, history, message, expected in ecommerce:
        p = copy.deepcopy(cases[0]['payload']);p['message'] = message
        p['conversation_context'] = {'recent_messages': [{'role': role, 'content': text, 'seq': i+1} for i,(role,text) in enumerate(history)]}
        p['domain_capabilities'][0]['description'] = 'Explain ecommerce customer service policies and product documentation using knowledge evidence.'
        cases.append({'id': name, 'payload': p, 'expected': expected, 'scope': 'Chinese ecommerce simulation, not real traffic'})
    old_source = subprocess.check_output(['git', 'show', '2b6b43f:infrastructure/target_conversation_provider.py'], text=True)
    # Historical provider uses the former scalar transport signature; preserve its single-message input.
    old_source = old_source.replace('content=request["messages"][0]["content"]', 'messages=[HumanMessage(request["messages"][0]["content"]) ]')
    old = types.ModuleType('baseline_planning');exec(compile(old_source, 'baseline_planning.py', 'exec'), old.__dict__)
    manifest = {'gap': 'R01', 'baseline': '2b6b43f', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'budget': 12, 'model': 'deepseek-v4-flash', 'retry': 0, 'cases': cases,
                'adoption': 'Original topic failure recovered and no new semantic regressions; manual review separately from schema.'}
    (ROOT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    values = {**dotenv_values('.env'), **os.environ};policy = ModelPolicy.from_env(values)
    profile = ModelProfile('deepseek-v4-flash', ReasoningEffort.NONE, 'deepseek')
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url,
                                     'max_retries': 0, 'timeout': 60}, max_tokens=800)
    capture = FrameworkCapture(limit=12)
    for case in cases:
        for arm, cls in [('baseline', old.AnthropicConversationPlanningProvider), ('candidate', AnthropicConversationPlanningProvider)]:
            provider = cls({ModelRole.INTENT: model}, model_profile=profile, synthesis_profile=profile, callbacks=(capture,))
            before = len(capture.calls)
            try: result = {'plan': await provider.plan(copy.deepcopy(case['payload']))}
            except Exception as exc: result = {'error_type': type(exc).__name__, 'error': str(exc)[:300]}
            result.update(id=case['id'], arm=arm, calls=clean(capture.calls[before:]))
            with (ROOT/'results.jsonl').open('a') as f: f.write(json.dumps(result, ensure_ascii=False)+'\n')
            print(case['id'], arm, result.get('plan', result.get('error_type')), flush=True)
    (ROOT/'results.jsonl.gz').write_bytes(gzip.compress((ROOT/'results.jsonl').read_bytes(), mtime=0))
if __name__ == '__main__': asyncio.run(main())
