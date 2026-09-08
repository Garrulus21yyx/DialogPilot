"""Build a pure RAG subset and run a fixed retrieval comparison, no business flows."""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path
from urllib.parse import quote
from core.model_policy import ModelRole


def build(root):
    data=Path('data/eval/ecommerce-rag-v1'); inputs=[];labels=[]
    for split in ['dev','heldout']:
        gold={c['id']:c for c in json.loads((data/f'{split}.gold.json').read_text())}
        for c in json.loads((data/f'{split}.inputs.json').read_text()):
            g=gold[c['id']]
            if g['category']=='mixed':continue
            assert not g['business_required']
            inputs.append(c);labels.append({**g,'original_split':split,'evaluation_split':'consumed_'+split})
    assert len(inputs)==100 and len({c['group_id'] for c in labels})==50
    root.mkdir(parents=True)
    for name,value in [('inputs.json',inputs),('labels.json',labels)]:
        (root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    return data


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--rewrite-cache',type=Path,required=True);args=p.parse_args()
    if args.output.exists():raise ValueError('output must be new')
    data=build(args.output)
    cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0]
    env=dict(x.split('=',1) for x in cfg['Config']['Env'] if '=' in x)
    runenv={**os.environ,'TEST_DATABASE_URL':'postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres','MODEL_PROVIDER':'deepseek','RAG_RERANKER':'local_bge','RAG_LOCAL_RERANKER_PATH':'/home/yang/.cache/dialogpilot-models/bge-reranker-v2-m3','RAG_LOCAL_RERANKER_DEVICE':'cuda','HF_HUB_OFFLINE':'1'}
    for role in ModelRole:
        runenv['MODEL_'+role.value.upper()]='deepseek-v4-flash';runenv['MODEL_'+role.value.upper()+'_REASONING']='none';runenv['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
    paths=subprocess.check_output(['git','ls-files','core','application','infrastructure','mcp','api'],text=True).splitlines()+['scripts/run_rag_tool_calibration.py','evaluation/ecommerce_pure_rag.py','scripts/run_ecommerce_pure_rag.py']
    (args.output/'lock.json').write_text(json.dumps({'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'source_hashes':{f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in paths if Path(f).is_file()},'data_manifest_sha256':hashlib.sha256((data/'manifest.json').read_bytes()).hexdigest(),'rewrite_cache_sha256':hashlib.sha256(args.rewrite_cache.read_bytes()).hexdigest(),'scope':'100 consumed synthetic pure knowledge cases; 50 groups; all 60 source documents retained','budget':{'api_max':50,'reranker':'local BGE','candidate_k':20,'final_k':5,'context_tokens':2600}},indent=2)+'\n')
    subprocess.run([sys.executable,'scripts/run_rag_tool_calibration.py','--model','/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181','--output',str(args.output/'runtime'),'--pure-rag-inputs',str(args.output/'inputs.json'),'--pure-rewrite-cache',str(args.rewrite_cache),'--corpus-file',str(data/'corpus.json'),'--max-api-calls','50'],env=runenv,check=True)
if __name__=='__main__':main()
