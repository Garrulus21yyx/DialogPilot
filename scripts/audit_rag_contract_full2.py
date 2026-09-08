"""Replay evidence visibility and citations; never equate verifier PASS with completeness."""
import json,re
from pathlib import Path
from mcp.tool_manager import MCPToolManager, ToolResult
ROOT=Path('artifacts/eval/rag-contract-full2-2026-09-08/run')
def main():
 rows=[json.loads(l) for l in (ROOT/'full-cases.jsonl').read_text().splitlines()]
 sources={'hypothesis':'synthetic-shop:opened-audio','switch':'synthetic-shop:invoice'}
 results=[]
 for r in rows:
  assert r['outcome_type']=='Completed' and len(r['tools'])==1
  tool=r['tools'][0];d=tool['result']['data'];pack=d['evidence_pack'];answer=r['outcome']['response']['response']
  wire=json.loads(MCPToolManager._render_for_model(None,ToolResult(True,d,'knowledge_search',authority='knowledge.active_source')))
  assert any(e['source']['source_id']==sources[r['case_id']] for e in wire['evidence'])
  labels={e['evidence_id'] for e in wire['evidence']}
  cited=set(re.findall(r'\[(E[^\]]+)\]',answer)); assert cited and cited<=labels
  assert tool['params']['query']==pack['query']
  results.append({'id':r['case_id'],'query':pack['query'],'gold_document_visible':True,'citations_exist':True,'answer':answer,'calls':len(r['api_calls']),
                  'manual_assessment':'Conditional policy response; no assertion that this user actually opened the item.' if r['case_id']=='hypothesis' else 'Application eligibility answered, operational steps missing in corpus and answer; response does not disclose that gap. Not full request completion.'})
 report={'n':2,'calls':sum(r['calls'] for r in results),'cases':results,'scope':'synthetic 31-document corpus; no baseline comparison or overall accuracy claim; visibility reconstructed through actual serializer, semantic assessment authored separately'}
 (ROOT/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print('2 evidence and citation replays passed; invoice completeness remains open')
if __name__=='__main__':main()
