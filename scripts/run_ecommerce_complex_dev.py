"""Build a pure RAG subset and run a fixed retrieval comparison, no business flows."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
from urllib.parse import quote
from core.model_policy import ModelRole


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--corpus',type=Path);p.add_argument('--rewrite-cache',type=Path);args=p.parse_args()
    if args.output.exists():raise ValueError('output must be new')
    data=Path('data/eval/ecommerce-complex-v2');args.output.mkdir(parents=True)
    (args.output/'inputs.json').write_bytes((data/'dev.inputs.json').read_bytes())
    pinned={'corpus':args.corpus or data/'corpus.json','inputs':args.output/'inputs.json'}
    if args.rewrite_cache:pinned['rewrite_cache']=args.rewrite_cache
    (args.output/'execution-inputs.json').write_text(json.dumps({k:{'path':str(v),'sha256':hashlib.sha256(v.read_bytes()).hexdigest()} for k,v in pinned.items()},indent=2)+'\n')
    cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
    env=dict(x.split('=',1) for x in cfg['Config']['Env'] if '=' in x)
    runenv={**os.environ,'TEST_DATABASE_URL':'postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres','PGOPTIONS':'-c max_parallel_maintenance_workers=0','MODEL_PROVIDER':'deepseek','RAG_RERANKER':'local_bge','RAG_LOCAL_RERANKER_PATH':'/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3','RAG_LOCAL_RERANKER_DEVICE':'cuda','HF_HUB_OFFLINE':'1'}
    for role in ModelRole:
        runenv['MODEL_'+role.value.upper()]='deepseek-v4-flash';runenv['MODEL_'+role.value.upper()+'_REASONING']='none';runenv['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
    paths=subprocess.check_output(['git','ls-files','core','application','infrastructure','mcp','api'],text=True).splitlines()+['scripts/run_rag_tool_calibration.py','evaluation/ecommerce_pure_rag.py','scripts/run_ecommerce_complex_dev.py']
    (args.output/'lock.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes':{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in paths if Path(f).is_file()},'data_manifest_sha256':hashlib.sha256((data/'manifest.json').read_bytes()).hexdigest(),'scope':'40 development complex synthetic questions / 10 rule families; heldout not loaded','budget':{'api_max':40,'reranker':'local BGE','candidate_k':20,'final_k':5,'context_tokens':2600}},indent=2)+'\n')
    subprocess.run([sys.executable,'scripts/run_rag_tool_calibration.py','--model','/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181','--output',str(args.output/'runtime'),'--pure-rag-inputs',str(args.output/'inputs.json'),'--corpus-file',str(args.corpus or data/'corpus.json'),'--max-api-calls','40']+(['--pure-rewrite-cache',str(args.rewrite_cache)] if args.rewrite_cache else []),env=runenv,check=True)
if __name__=='__main__':main()
