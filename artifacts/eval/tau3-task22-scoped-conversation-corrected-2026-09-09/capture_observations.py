"""Read logged generations for this failed run; no model or business calls."""
import json
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values

env = dict(os.environ)
env.update({k:v for k,v in dotenv_values('.env').items() if k.startswith('LANGFUSE_') and v})
env['LANGFUSE_HOST'] = env['LANGFUSE_BASE_URL']
args = ['npx','--yes','langfuse-cli','api','observations','list', '--session-id',
        'tau3-3bd8bdead27f4d138b687dd4d890242e','--type','GENERATION',
        '--fields','core,basic,io,model','--limit','100','--json']
rows=[]
cursor=None
while True:
    response = subprocess.run(args + (['--cursor',cursor] if cursor else []),env=env,capture_output=True,text=True,check=True)
    body=json.loads(response.stdout)['body']
    rows.extend(body['data'])
    cursor=body.get('meta',{}).get('cursor')
    if not cursor:
        break
records=[]
for row in rows:
    record={k:row.get(k) for k in ('id','name','startTime','parentObservationId','traceId','input','output','providedModelName')}
    for key in ('input','output'):
        if isinstance(record[key],str):
            try: record[key]=json.loads(record[key])
            except json.JSONDecodeError: pass
    records.append(record)
Path(__file__).with_name('observations.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
print(json.dumps([{'id':r['id'],'name':r['name'],'time':r['startTime']} for r in records]))
