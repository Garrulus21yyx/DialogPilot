"""Explicit frozen paired acceptance in isolated source snapshots and databases."""
import argparse,json,os,shutil,subprocess,sys,hashlib
from pathlib import Path
from urllib.parse import quote
from dotenv import dotenv_values
from core.model_policy import ModelRole
ROOT=Path.cwd()
OLD='For knowledge-based statements cite the supplied public evidence labels as [E...]. '
NEW='For each knowledge-based statement, copy the complete evidence_id from its supporting evidence, character for character, and enclose it in square brackets. Use only supplied allowed_evidence_ids; retain every character of the label. '
def main():
 p=argparse.ArgumentParser();p.add_argument('--freeze',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 freeze=json.loads(a.freeze.read_text());assert freeze['development_gate']=='PASSED' and freeze['heldout_cases']==80
 for f,h in freeze['source_hashes'].items():assert hashlib.sha256(Path(f).read_bytes()).hexdigest()==h,f
 if a.output.exists():raise ValueError('fresh output required')
 a.output.mkdir(parents=True);a.output=a.output.resolve()
 config=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];env=dict(x.split('=',1) for x in config['Config']['Env'] if '=' in x)
 runenv={**{k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None},**os.environ,'TEST_DATABASE_URL':'postgresql://'+quote(env.get('POSTGRES_USER','postgres'),safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/postgres','MODEL_PROVIDER':'deepseek','RAG_RERANKER':'listwise','HF_HUB_OFFLINE':'1'}
 for role in ModelRole:
  runenv['MODEL_'+role.value.upper()]='deepseek-v4-flash';runenv['MODEL_'+role.value.upper()+'_REASONING']='none';runenv['MODEL_'+role.value.upper()+'_MIN_COMPLETION_TOKENS']='0'
 snapshots=[]
 for arm in ['baseline','candidate']:
  snap=Path('/tmp')/('dialogpilot-ecommerce-'+a.output.name+'-'+arm)
  if snap.exists():raise ValueError('snapshot exists')
  snap.mkdir()
  for d in ['api','application','infrastructure','core','mcp','memory','services','evaluation','scripts','configs','config','utils','agents','governance','migrations','monitor','tools']:
   if (ROOT/d).is_dir():shutil.copytree(ROOT/d,snap/d,ignore=shutil.ignore_patterns('__pycache__'))
  for d in ['data','artifacts','skills']:
   if (ROOT/d).exists():(snap/d).symlink_to(ROOT/d,target_is_directory=True)
  for f in ROOT.iterdir():
   if f.is_file() and f.suffix in ('.py','.toml','.ini','.yaml','.yml'):shutil.copy2(f,snap/f.name)
  if arm=='baseline':
   f=snap/'infrastructure/target_conversation_provider.py';s=f.read_text();assert s.count(NEW)==1;f.write_text(s.replace(NEW,OLD))
   f=snap/'application/response_assembly.py';s=f.read_text();assert s.count('if evidence_content_identity(prior) != evidence_content_identity(item):')==1;f.write_text(s.replace('if evidence_content_identity(prior) != evidence_content_identity(item):','if prior != item:'))
  identities={str(f.relative_to(snap)):hashlib.sha256(f.read_bytes()).hexdigest() for folder in ['api','application','infrastructure','core','mcp','memory','services','evaluation','scripts'] for f in (snap/folder).rglob('*.py')}
  (a.output/(arm+'-source-lock.json')).write_text(json.dumps(identities,indent=2)+'\n');snapshots.append((arm,snap))
 shutil.copy2(a.freeze,a.output/'freeze.json')
 # Each arm uses a separately created evaluation DB. Parallelism is bounded at 2.
 running=[]
 for arm,snap in snapshots:
  log=(a.output/(arm+'.log')).open('w');cmd=[sys.executable,str(snap/'scripts/run_rag_tool_calibration.py'),'--model','/home/yang/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181','--output',str(a.output/arm),'--scenario','basic','--full-chain','--full-case-file',str(ROOT/'data/eval/ecommerce-rag-v1/heldout.inputs.json'),'--corpus-file',str(ROOT/'data/eval/ecommerce-rag-v1/corpus.json'),'--business-fixtures',str(ROOT/'data/eval/ecommerce-rag-v1/heldout.fixtures.json'),'--max-api-calls','400']
  running.append((arm,subprocess.Popen(cmd,cwd=snap,env={**runenv,'PYTHONPATH':str(snap)},stdout=log,stderr=subprocess.STDOUT),log))
 outcomes={}
 for arm,process,log in running:outcomes[arm]=process.wait();log.close()
 (a.output/'execution.json').write_text(json.dumps(outcomes,indent=2)+'\n');assert all(v==0 for v in outcomes.values()),outcomes
if __name__=='__main__':main()
