"""Dataset boundary invariants, without running LLMs or importing gold into runtime."""
import ast,json,hashlib
from pathlib import Path
import pytest
ROOT=Path('data/eval/ecommerce-rag-v1')
@pytest.mark.parametrize('split,n,groups',[('dev',40,20),('heldout',80,40)])
def test_input_gold_separation_and_group_counts(split,n,groups):
 rows=json.loads((ROOT/f'{split}.inputs.json').read_text());assert len(rows)==n
 assert len({r['group_id'] for r in rows})==groups
 for row in rows:assert set(row)=={'id','group_id','category','history','message'}
def test_groups_disjoint_and_locked_content():
 load=lambda n:json.loads((ROOT/n).read_text())
 assert {r['group_id'] for r in load('dev.inputs.json')}.isdisjoint({r['group_id'] for r in load('heldout.inputs.json')})
 for n,h in load('manifest.json')['sha256'].items():assert hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h
 corpus={r['source_id']:r['content'] for r in load('corpus.json')}
 for split in ['dev','heldout']:
  for r in load(split+'.gold.json'):
   for sp in r['source_spans']:assert corpus[sp['source_id']][sp['start']:sp['end']]==sp['quote']
def test_runtime_loader_does_not_read_gold_or_heldout():
 tree=ast.parse(Path('scripts/run_ecommerce_rag_acceptance.py').read_text())
 values=[n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str)]
 assert not any('.gold.json' in v or 'heldout.inputs.json' in v for v in values)
