"""Falsify lexical preservation as a semantic guarantee; no model calls."""
import json
from pathlib import Path
from mcp.query_requirements import QueryRequirementPlanner,_RequirementDeps,_StructuredRequirementOutput,_identifiers,_negations
CASES=[
 ('dropped_negation','哪些国家不支持发货',['哪些国家支持发货'],'simple','UNSAFE'),
 ('scope_shift','哪些国家不支持发货',['哪些国家支持发货且不收运费'],'simple','UNSAFE'),
 ('double_negation','哪些国家不支持发货',['哪些国家不是不支持发货'],'simple','UNSAFE'),
 ('faithful','哪些国家不支持发货',['不支持配送的国家有哪些'],'simple','SAFE'),
 ('invented_condition','价格是多少',['iPhone16在德国的价格是多少'],'simple','UNSUPPORTED_ASSUMPTION'),
 ('condition_detached','非质量问题的拆封耳机能退吗',['拆封耳机怎么退','非质量问题怎么处理'],'parallel_composite','LOST_CONDITION_CONJUNCTION'),
 ('acronym','GPT与BERT有什么区别',['Generative Pre-trained Transformer与Bidirectional Encoder Representations from Transformers有什么区别'],'simple','EXPANSION_RECALL_RISK_NOT_LOGIC_ERROR'),
]
def main():
 rows=[]
 for name,raw,queries,kind,semantic in CASES:
  try:
   QueryRequirementPlanner._require_valid(_StructuredRequirementOutput(query_kind=kind,requirements=queries),_RequirementDeps(_identifiers(raw),_negations(raw)));accepted=True;error=''
  except ValueError as e:accepted=False;error=str(e)
  rows.append({'case':name,'raw':raw,'queries':queries,'known_semantic_property':semantic,'lexical_guard_accepted':accepted,'error':error})
 p=Path('artifacts/eval/rag-raw-standalone300-2026-09-08/negative-guard.json');p.write_text(json.dumps({'scope':'Existing requirement planner validator only; not production rewrite guarantee. Labels authored for counterexamples, no model calls.','cases':rows},ensure_ascii=False,indent=2)+'\n');print(json.dumps(rows,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
