"""Capture logged, redacted author observations; never executes business tools."""
import json
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values

env = dict(os.environ)
env.update({k: v for k, v in dotenv_values('.env').items() if k.startswith('LANGFUSE_') and v})
env['LANGFUSE_HOST'] = env['LANGFUSE_BASE_URL']
result = subprocess.run(['npx', '--yes', 'langfuse-cli', 'api', 'observations', 'list',
    '--session-id', 'tau3-2928d699366646048d853e01e0d1d686', '--type', 'GENERATION',
    '--fields', 'core,basic,io,model', '--limit', '100', '--json'],
    env=env, capture_output=True, text=True, check=True)
records = []
for row in json.loads(result.stdout)['body']['data']:
    if row['id'] not in {'cd42cc02de79d3da', '51efc55e97af9e7f'}:
        continue
    record = {key: row.get(key) for key in ('id', 'name', 'providedModelName', 'input', 'output')}
    for key in ('input', 'output'):
        if isinstance(record[key], str):
            try:
                record[key] = json.loads(record[key])
            except json.JSONDecodeError:
                pass
    records.append(record)
Path(__file__).with_name('source.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
print(json.dumps([{'id': r['id'], 'name': r['name'], 'input_type': type(r['input']).__name__} for r in records]))
