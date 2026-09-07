"""Run the four predeclared PG chunk profiles in isolated model processes."""
import argparse,subprocess,sys
from pathlib import Path
from evaluation.postgres_rag_candidate_eval import CHUNK_PROFILES


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);p.add_argument('--locale',default='en')
    args=p.parse_args()
    for profile in CHUNK_PROFILES:
        # Each child owns model/GPU lifetime. Environment and dedicated-DB
        # preflight are checked by the existing runner; secrets are not printed.
        subprocess.run([sys.executable,'-m','scripts.run_postgres_rag_eval','--dataset',str(args.dataset),
            '--split','dev','--locale',args.locale,'--chunk-config',profile,
            '--output',str(args.output/profile)],check=True)
if __name__=='__main__':main()
