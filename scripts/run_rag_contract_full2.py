"""Two frozen contract cases on the actual isolated durable RAG runtime."""
import json, os, subprocess, sys
from pathlib import Path
from urllib.parse import quote
from core.model_policy import ModelRole
OUT=Path('artifacts/eval/rag-contract-full2-2026-09-08')
def main():
 OUT.mkdir(parents=True,exist_ok=False)
 manifest=json.loads(Path('artifacts/eval/rag-evidence-contract12-2026-09-08/manifest.json').read_text())
 required={'hypothesis':['已拆封','非质量原因','不适用无理由退货'], 'switch':['订单完成后','申请电子发票']}
 cases=[{'id':c['id'],'history':[(h['role'],h['content']) for h in c['history']], 'message':c['query'],'required':required[c['id']]} for c in manifest['cases'] if c['id'] in required]
 (OUT/'cases.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
 config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
 env=dict(x.split('=',1) for x in config['Config']['Env'] if '=' in x)
 runenv={**os.environ,'TEST_DATABASE_URL':'postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres','MODEL_PROVIDER':'deepseek','RAG_RERANKER':'listwise','RAG_VECTOR_WEIGHT':'0.5','RAG_LEXICAL_WEIGHT':'0.5','HF_HUB_OFFLINE':'1'}
 for role in ModelRole:
  runenv['MODEL_'+role.value.upper()]='deepseek-v4-flash';runenv['MODEL_'+role.value.upper()+'_REASONING']='none';runenv['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
 subprocess.run([sys.executable,'scripts/run_rag_tool_calibration.py','--model','/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181','--output',str(OUT/'run'),'--scenario','ecommerce-full','--full-chain','--full-case-file',str(OUT/'cases.json'),'--max-api-calls','16'],env=runenv,check=True)
if __name__=='__main__': main()
