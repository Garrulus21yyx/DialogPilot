"""Positive author and judge controls; never calls business tools."""
import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole, ReasoningEffort
from core.structured_model import structured_call
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_conversation_provider import AnthropicConversationPlanningProvider

ROOT = Path(__file__).parent

async def main():
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    profile = replace(policy.profile(ModelRole.VERIFIER), reasoning=ReasoningEffort.HIGH,
                      min_completion_tokens=8192)
    model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'],
                            'base_url': policy.base_url}, max_tokens=8192)
    rows = json.loads((ROOT/'source.json').read_text())
    async def run(row, mode):
        capture = FrameworkCapture(limit=1)
        result = {'id': row['observation_id'], 'mode': mode, 'profile': profile.to_dict()}
        request = json.loads(row['messages'][1]['content'])
        try:
            async with asyncio.timeout(180):
                if mode == 'compose':
                    provider = AnthropicConversationPlanningProvider({ModelRole.SYNTHESIS: model},
                        model_profile=profile, synthesis_profile=profile, max_tokens=8192, callbacks=(capture,))
                    result['output'] = await provider.compose({
                        'current_message': request['question'], 'evidence': request['evidence']['context'],
                        'domain_notes': [], 'response_requirements': [],
                    })
                else:
                    request['answer'] = ('Your default account address change is prepared but has not been executed. '
                        'It will use the new address details you supplied, with city New York, state NY, ZIP 10001, USA. '
                        'Do you approve this account address change? The shipping address change for order #W9911714 '
                        'is not prepared and is not included in this approval.'
                        if request['evidence']['approval_required'] else 'Which state should I use for the new address?')
                    result['input_answer'] = request['answer']
                    result['output'] = await structured_call(model, name='submit_claim_checks',
                        schema=row['messages'][2]['content']['function']['parameters']['properties']['result'],
                        system=row['messages'][0]['content'],
                        messages=[HumanMessage(json.dumps(request))], callbacks=(capture,))
        except Exception as exc:
            result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['calls'] = capture.calls
        with (ROOT/'positive.jsonl').open('a') as stream:
            stream.write(json.dumps(result, default=str)+'\n')
        print(result['id'], mode, result.get('output', result.get('error')), flush=True)
    await asyncio.gather(*(run(row, mode) for row in rows for mode in ('compose', 'verify')))

if __name__ == '__main__':
    asyncio.run(main())
