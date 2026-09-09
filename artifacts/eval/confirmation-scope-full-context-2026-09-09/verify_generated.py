"""Existing author/verification revision path on saved generated candidates."""
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from dotenv import dotenv_values
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider
from services.answer_verifier import AnswerVerifier

ROOT = Path(__file__).parent

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    config = {'api_key': values['ANTHROPIC_API_KEY'], 'base_url': policy.base_url}
    models = {role: framework_model(policy.profile(role), config) for role in (ModelRole.SYNTHESIS, ModelRole.VERIFIER)}
    sources = {row['observation_id']: json.loads(row['messages'][1]['content'])
               for row in json.loads((ROOT/'source.json').read_text())}
    async def run(row):
        source = sources[row['id']]
        capture = FrameworkCapture(limit=3)
        verifier = AnswerVerifier(models[ModelRole.VERIFIER], model_profile=policy.profile(ModelRole.VERIFIER), callbacks=(capture,))
        provider = AnthropicConversationPlanningProvider(models, model_profile=policy.profile(ModelRole.SYNTHESIS),
            synthesis_profile=policy.profile(ModelRole.SYNTHESIS), callbacks=(capture,))
        result = {'id': row['id'], 'original_candidate': row['output']}
        try:
            async with asyncio.timeout(300):
                verdict = await verifier.verify(source['question'], row['output'], json.dumps(source['evidence']['context']))
                result['first_verdict'] = asdict(verdict)
                if not verdict.publishable and verdict.assessment is not None:
                    answer = await provider.compose({'current_message': source['question'], 'evidence': source['evidence']['context'],
                        'domain_notes': [], 'response_requirements': [],
                        'repair_feedback': {'previous_answer': row['output'], 'assessment': asdict(verdict.assessment),
                                            'reason_code': verdict.reason_code.value, 'reason': verdict.reason}})
                    result['revised_candidate'] = answer
                    result['revised_verdict'] = asdict(await verifier.verify(source['question'], answer, json.dumps(source['evidence']['context'])))
        except Exception as exc:
            result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['calls'] = capture.calls
        with (ROOT/'verified_generated.jsonl').open('a') as stream:
            stream.write(json.dumps(result, default=str)+'\n')
        print(result['id'], {k:v for k,v in result.items() if k != 'calls'}, flush=True)
    rows = [json.loads(line) for line in (ROOT/'positive.jsonl').read_text().splitlines() if json.loads(line)['mode'] == 'compose']
    await asyncio.gather(*(run(row) for row in rows))

if __name__ == '__main__':
    asyncio.run(main())
