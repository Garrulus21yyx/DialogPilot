"""Development-only real durable runtime. Gold is deliberately never loaded."""
import json,os,subprocess,sys,argparse,hashlib
from pathlib import Path
from urllib.parse import quote
from core.model_policy import ModelRole
from scripts.build_ecommerce_rag_acceptance import ROOT

def main():
 p=argparse.ArgumentParser();p.add_argument('--phase',choices=['calibration','development','baseline'],required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
 if args.output.exists():raise ValueError('output exists')
 rows=json.loads((ROOT/'dev.inputs.json').read_text());fx=json.loads((ROOT/'dev.fixtures.json').read_text())
 chosen=['ec-001-1','ec-016-2','ec-028-1','ec-041-1']
 if args.phase=='calibration':
  rows=[r for r in rows if r['id'] in chosen];assert len(rows)==4
 elif args.phase=='development':
  rows=[r for r in rows if r['id'] not in chosen];assert len(rows)==36
 ids={r['id'] for r in rows};fx=[r for r in fx if r['id'] in ids]
 args.output.mkdir(parents=True);inputs=args.output/'runtime-inputs.json';inputs.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n');fixtures=args.output/'runtime-fixtures.json';fixtures.write_text(json.dumps(fx,ensure_ascii=False,indent=2)+'\n')
 config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];env=dict(x.split('=',1) for x in config['Config']['Env'] if '=' in x)
 runenv={**os.environ,'TEST_DATABASE_URL':'postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres','MODEL_PROVIDER':'deepseek','RAG_RERANKER':'listwise'}
 for role in ModelRole:
  runenv['MODEL_'+role.value.upper()]='deepseek-v4-flash';runenv['MODEL_'+role.value.upper()+'_REASONING']='none';runenv['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
 tracked=subprocess.check_output(['git','ls-files','application','infrastructure','services','core','mcp','api'],text=True).splitlines()+['evaluation/rag_full_chain_probe.py','scripts/run_rag_tool_calibration.py','scripts/run_ecommerce_rag_acceptance.py']
 (args.output/'run-lock.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'phase':args.phase,'n':len(rows),'max_api_calls':40 if args.phase=='calibration' else 320,'dataset_manifest_sha256':hashlib.sha256((ROOT/'manifest.json').read_bytes()).hexdigest(),'source_hashes':{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in tracked if Path(f).is_file()},'gold_loaded':False},indent=2)+'\n')
 subprocess.run([sys.executable,'scripts/run_rag_tool_calibration.py','--model','/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181','--output',str(args.output/'runtime'),'--scenario','basic','--full-chain','--full-case-file',str(inputs),'--corpus-file',str(ROOT/'corpus.json'),'--business-fixtures',str(fixtures),'--max-api-calls','40' if args.phase=='calibration' else '320'],env=runenv,check=True)
if __name__=='__main__':main()
