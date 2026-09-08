import hashlib,json,os,subprocess,sys
from pathlib import Path
from urllib.parse import quote
from core.model_policy import ModelRole
root=Path('artifacts/eval/rag-current-entry2-2026-09-08');root.mkdir(exist_ok=False)
cases=Path('artifacts/eval/rag-g4-ecommerce-pair2-2026-09-08/cases.json')
(root/'cases.json').write_bytes(cases.read_bytes())
config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
env=dict(x.split('=',1) for x in config['Config']['Env'] if '=' in x)
runenv={**os.environ,'TEST_DATABASE_URL':'postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres','MODEL_PROVIDER':'deepseek','RAG_RERANKER':'listwise','RAG_VECTOR_WEIGHT':'0.25','RAG_LEXICAL_WEIGHT':'0.75','HF_HUB_OFFLINE':'1'}
for role in ModelRole:
 runenv['MODEL_'+role.value.upper()]='deepseek-v4-flash'
 runenv['MODEL_'+role.value.upper()+'_REASONING']='none'
 runenv['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
files=subprocess.check_output(['git','ls-files','application','infrastructure','core','services','evaluation/rag_full_chain_probe.py'],text=True).splitlines()
(root/'source-identity.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'files':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in files if Path(p).is_file()}},indent=2)+'\n')
subprocess.run([sys.executable,'scripts/run_rag_tool_calibration.py','--model','/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181','--output',str(root/'run'),'--scenario','ecommerce-full','--full-chain','--full-case-file',str(root/'cases.json'),'--max-api-calls','12'],env=runenv,check=True)
