"""Offline measurement of pinned vs editable context in captured reviewer inputs."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from infrastructure.target_model_context import delegated_working_input
from scripts.measure_domain_review_context import payloads


def measure(data):
    rows = []
    for payload in payloads(data):
        for message in payload['working_context']:
            content = message.get('content')
            if message.get('role') != 'human' or not isinstance(content, list):
                continue
            sections = {}
            for block in content:
                if isinstance(block, dict) and block.get('type') == 'text':
                    try:
                        decoded = json.loads(block['text'])
                    except (ValueError, KeyError):
                        continue
                    if isinstance(decoded, dict):
                        sections.update(decoded)
            if not {'source_context', 'runtime_context', 'delegated_task'} <= sections.keys():
                continue
            working, pinned = delegated_working_input([], content, 'audit')
            rows.append({'outcome': payload['proposed_outcome'],
                'old_pinned_tokens': count_tokens_approximately([HumanMessage(content)]),
                'new_pinned_tokens': count_tokens_approximately([pinned]),
                'editable_background_tokens': count_tokens_approximately(working[:-1]),
                'new_total_tokens': count_tokens_approximately(working),
                'domain_review_required_now': payload['proposed_outcome'] == 'PREPARE_ACTION'})
    return rows


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    args = parser.parse_args()
    rows = measure(json.loads(args.input.read_text()))
    if not rows:
        raise SystemExit('No supported task context captured')
    print(json.dumps({'scope': 'offline partition; total is not claimed smaller before editing',
                      'rows': rows}, indent=2))
