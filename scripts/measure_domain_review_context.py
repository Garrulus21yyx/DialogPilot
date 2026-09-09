"""Offline before/after projection audit; optional read-only Langfuse CLI capture.

No model calls. Artifacts contain development observation inputs: keep local.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from langchain_core.messages import HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from infrastructure.domain_review_context import project_review_context


def audit(payload):
    projected = project_review_context(payload)
    before = json.dumps(payload, ensure_ascii=False, default=str)
    after = json.dumps(projected, ensure_ascii=False, separators=(",", ":"), default=str)
    for key in payload.keys() - {"capabilities", "working_context"}:
        assert projected[key] == payload[key], key
    assert payload['capabilities'] == projected['capabilities']
    assert len(payload['working_context']) == len(projected['working_context'])
    for original, result in zip(payload['working_context'], projected['working_context']):
        restored = json.loads(json.dumps(result))
        if 'content_json' in restored:
            assert json.loads(json.dumps(restored.pop('content_json')), parse_float=Decimal) == json.loads(original['content'], parse_float=Decimal)
            restored['content'] = original['content']
        if isinstance(restored.get('content'), list):
            for old_block, block in zip(original['content'], restored['content'], strict=True):
                if isinstance(block, dict) and 'text_json' in block:
                    assert json.loads(json.dumps(block.pop('text_json')), parse_float=Decimal) == json.loads(old_block['text'], parse_float=Decimal)
                    block['text'] = old_block['text']
        if restored.pop('content_ref', None):
            restored['content'] = payload['candidate']
        for call in restored.get('tool_calls', []):
            if call.pop('arguments_ref', None):
                call['args'] = payload['candidate']['arguments']
        if original.get('role') == 'tool' and original.get('content') != restored.get('content'):
            assert json.loads(original['content']) == json.loads(restored['content'])
            restored['content'] = original['content']
        assert restored == original
    count = lambda text: count_tokens_approximately([HumanMessage(text)])
    return {'before_tokens': count(before), 'after_tokens': count(after),
            'saved_tokens': count(before)-count(after), 'preservation_passed': True,
            'source_sha256': hashlib.sha256(before.encode()).hexdigest()}


def payloads(data):
    # CLI envelope plus generation input (JSON encoded by Langfuse).
    if isinstance(data, str):
        try: data = json.loads(data)
        except ValueError: return
    if isinstance(data, dict):
        if {'working_context', 'capabilities', 'proposed_outcome'} <= data.keys():
            yield data
        else:
            for key in ('body', 'data', 'input', 'content', 'messages', 'kwargs'):
                if key in data: yield from payloads(data[key])
    elif isinstance(data, list):
        for item in data: yield from payloads(item)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', action='append', default=[])
    parser.add_argument('--input', type=Path, action='append', default=[])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = []
    for session in args.session:
        env = dict(os.environ)
        for key, value in dotenv_values(ROOT/'.env').items():
            if key.startswith('LANGFUSE_') and value: env.setdefault(key, value)
        if env.get('LANGFUSE_BASE_URL'): env['LANGFUSE_HOST'] = env['LANGFUSE_BASE_URL']
        response = subprocess.check_output(['npx', '--yes', 'langfuse-cli', 'api', 'observations', 'list',
            '--session-id', session, '--type', 'GENERATION', '--fields', 'core,basic,io',
            '--all', '--max-items', '200', '--json'], env=env, text=True)
        data = json.loads(response)
        (args.output/(session+'.json')).write_text(json.dumps(data, ensure_ascii=False))
        records.extend((session, value) for value in payloads(data))
    for path in args.input:
        records.extend((str(path), value) for value in payloads(json.loads(path.read_text())) )
    rows = [dict(source=source, **audit(value)) for source, value in records]
    if not rows:
        raise SystemExit('No review inputs found; no before/after result available.')
    result = {'scope':'offline request projection; not provider usage or model quality',
              'count':len(rows), 'rows':rows}
    (args.output/'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result, ensure_ascii=False))
