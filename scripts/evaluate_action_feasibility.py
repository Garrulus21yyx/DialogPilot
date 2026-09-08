"""Development-only state-transition probes of the production domain reviewer.

No business tools execute. Synthetic bounded cases diagnose reasoning configuration;
they are not held-out customer success scores or a replacement for tau evaluation.
"""
import argparse
import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage

from core.framework_models import framework_model
from core.model_policy import ModelPolicy, ModelRole, ReasoningEffort
from evaluation.framework_capture import FrameworkCapture
from infrastructure.target_domain_outcome import DomainOutcomeReview, SYSTEM


CASES = (
    {"id": "same_object_conflict", "goal": "Perform A and B on object X.",
     "policy": "A requires state ready and changes it to closed. B requires state ready and changes it to finished. Neither state transition can be reversed.",
     "candidate": "A", "expected": False},
    {"id": "independent_objects", "goal": "Perform A on object X and B on object Y.",
     "policy": "A requires state ready and changes that object's state to closed. B requires state ready and changes that object's state to finished. Operations do not affect other objects.",
     "candidate": "A", "expected": True},
    {"id": "valid_sequence", "goal": "Perform A and B on object X.",
     "policy": "A requires state ready and changes it to reviewed. B requires state reviewed and changes it to finished.",
     "candidate": "A", "expected": True},
    {"id": "wrong_sequence", "goal": "Perform A and B on object X.",
     "policy": "A requires state ready and changes it to reviewed. B requires state reviewed and changes it to finished.",
     "candidate": "B", "expected": False},
)


async def run(output):
    values = {**dotenv_values('.env'), **os.environ}
    policy = ModelPolicy.from_env(values)
    base = policy.profile(ModelRole.VERIFIER)
    profiles = [replace(base, reasoning=effort, min_completion_tokens=4096)
                for effort in (ReasoningEffort.NONE, ReasoningEffort.HIGH)]
    output.mkdir(parents=True, exist_ok=False)
    manifest = {"scope": __doc__, "cases": CASES, "profiles": [p.to_dict() for p in profiles],
                "max_api_calls": 8, "max_tokens_per_call": 4096,
                "system_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
                "production_config_changed": False, "business_tools_enabled": False}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    tools = [SimpleNamespace(name='prepare_' + action,
        description='Prepare operation ' + action + ' without executing it.',
        tool_call_schema={"type": "object", "properties": {"object_id": {"type": "string"}},
                          "required": ["object_id"], "additionalProperties": False}) for action in ('A', 'B')]
    for profile in profiles:
        model = framework_model(profile, {'api_key': values['ANTHROPIC_API_KEY'],
                                           'base_url': policy.base_url}, max_tokens=4096)
        for case in CASES:
            capture = FrameworkCapture(limit=1)
            reviewer = DomainOutcomeReview(model, callbacks=(capture,), available_tokens=24000,
                                           business_policy=case['policy'], tools=tools)
            context = SimpleNamespace(work_item=SimpleNamespace(work_item_id=case['id'],
                control=None, objective=case['goal'], arguments=()), trusted_context={}, dependency_results=())
            row = {'id': case['id'], 'reasoning': profile.reasoning.value, 'expected': case['expected']}
            try:
                async with asyncio.timeout(90):
                    row['assessment'] = await reviewer.assess(context=context,
                        messages=[HumanMessage('Objects X and Y currently have state ready. ' + case['goal'])],
                        kind='PREPARE_ACTION', candidate={'tool': 'prepare_' + case['candidate'],
                                                       'arguments': {'object_id': 'X'}})
                row['correct'] = row['assessment']['accepted'] == case['expected']
            except Exception as exc:
                row.update(error=type(exc).__name__, detail=str(exc), correct=False)
            row['calls'] = capture.calls
            with (output / 'results.jsonl').open('a') as stream:
                stream.write(json.dumps(row, ensure_ascii=False, default=str) + '\n')
            print(case['id'], profile.reasoning.value, row['correct'], flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(run(parser.parse_args().output))
