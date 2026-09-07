#!/usr/bin/env python3
"""Replay one captured simulator LLM call, without starting business execution."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path, nargs='?')
    parser.add_argument('--trajectory', type=Path, help='Reconstruct uncaptured historical input; not an exact replay')
    parser.add_argument('--tau-source', type=Path)
    parser.add_argument('--task-id')
    parser.add_argument('--current-simulator-policy', action='store_true',
                        help='Explicit policy comparison, not exact replay of old parameters')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if bool(args.capture) == bool(args.trajectory):
        parser.error('choose a captured request OR a historical trajectory')
    if args.trajectory and (not args.tau_source or args.task_id is None):
        parser.error('historical reconstruction requires --tau-source and --task-id')
    from dotenv import dotenv_values
    values = {**dotenv_values(ROOT / '.env'), **os.environ}
    for key, value in values.items():
        if key.startswith('LANGFUSE_') and value:
            os.environ.setdefault(key, value)
    from core.model_policy import ModelPolicy
    from evaluation.tau3_user_diagnostics import SimulatorDiagnostics, replay_user_call, simulator_parameters
    from litellm import completion
    if args.capture:
        record = json.loads(args.capture.read_text())
    else:
        os.environ['TAU2_DATA_DIR'] = str(args.tau_source.resolve() / 'data')
        sys.path.insert(0, str(args.tau_source.resolve() / 'src'))
        from tau2.domains.retail.environment import get_tasks
        from tau2.user.user_simulator import UserSimulator
        from tau2.data_model.message import AssistantMessage, UserMessage
        from tau2.utils.llm_utils import to_litellm_messages
        manifest = json.loads((args.trajectory.parent / 'manifest.json').read_text())
        task = next(t for t in get_tasks('train') if t.id == args.task_id)
        user = UserSimulator(llm=manifest['user_model'], instructions=str(task.user_scenario))
        history = [({'assistant': AssistantMessage, 'user': UserMessage}[m['role']])(
            role=m['role'], content=m['content']) for m in json.loads(args.trajectory.read_text())
            if m['role'] in {'assistant', 'user'} and m.get('content') and not m.get('tool_calls')]
        state = user.get_init_state(history)
        record = {'schema': 'tau3-simulator-call-v1', 'task_id': args.task_id,
            'session_id': 'historical-user-probe-' + args.task_id,
            'reconstruction': 'visible transcript + official default persona; not original raw request',
            'request': {'model': manifest['user_model'],
                'messages': to_litellm_messages(state.system_messages + state.flip_roles()),
                'temperature': 0, 'thinking': {'type': 'disabled'},
                'drop_params': True,
                'max_tokens': manifest['user_max_tokens'], 'timeout': 60, 'seed': manifest['seed']}}
    if args.current_simulator_policy:
        record['request'].update(simulator_parameters(record['request']['max_tokens']))
        record['parameter_override'] = 'current simulator policy; not original run'
    diagnostics = SimulatorDiagnostics(args.output,
        langfuse=values.get('LANGFUSE_ENABLED', '').lower() in {'1', 'true', 'yes'},
        secrets=[v for k, v in values.items() if any(part in k for part in ('KEY', 'SECRET', 'PASSWORD'))])
    try:
        (args.output / 'source.json').write_text(json.dumps(diagnostics.sanitize(record), indent=2) + '\n')
        replay_user_call(record, api_key=values['ANTHROPIC_API_KEY'],
            api_base=ModelPolicy.from_env(values).base_url, diagnostics=diagnostics, completion=completion)
    finally:
        print(json.dumps(diagnostics.task_summary(record['task_id'])))
        (args.output / 'trace-flush.json').write_text(json.dumps(diagnostics.close()) + '\n')


if __name__ == '__main__':
    main()
