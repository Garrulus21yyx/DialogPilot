"""Recompute stage signals from mixed application captures; no model grading."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re


def report(capture,definitions):
    raw=gzip.decompress(capture.read_bytes()) if capture.suffix=='.gz' else capture.read_bytes()
    rows=[json.loads(line) for line in raw.splitlines()]
    expected={r['case_id']:r for r in json.loads(definitions.read_text())}
    if len({r['case_id'] for r in rows})!=len(rows) or any(r['case_id'] not in expected for r in rows):
        raise ValueError('duplicate or unknown captured case')
    results=[]
    for row in rows:
        definition=expected[row['case_id']]
        response=row['outcome'].get('response',{})
        names=[t['name'] for t in row['tools']]
        visible=[]
        for tool in row['tools']:
            if tool['name']=='knowledge_search':
                try:
                    value=json.loads(tool['result'].get('output_for_model') or '{}')
                    visible.extend(value.get('evidence',[]))
                except (ValueError,TypeError):pass
        gold=[e for e in visible if e.get('source',{}).get('source_id')==definition['expected_source_id']]
        text=response.get('response','')
        cited=set(re.findall(r'\[(E[a-zA-Z0-9]+)\]',text))
        first=row['api_calls'][0] if row['api_calls'] else {}
        planner_stop=first.get('response',{}).get('stop_reason')
        result={'case_id':row['case_id'],'outcome':row['outcome_type'],'tool_names':names,
            'expected_tool_coverage':set(definition['expected_tools'])<=set(names),
            'expected_source_visible':bool(gold),
            'expected_source_cited':any(e['evidence_id'] in cited for e in gold),
            'knowledge_support_checked':response.get('synthesis_reason')=='KNOWLEDGE_SUPPORT_CHECKED',
            'synthesis_reason':response.get('synthesis_reason'),
            'planner_stop':planner_stop,'api_calls':len(row['api_calls']),
            'response':text,'latency_ms':response.get('latency_ms'),
            'visible_evidence_count':len(visible)}
        if row['outcome_type']!='Completed':
            result['failure_stage']='planner_truncation' if planner_stop=='max_tokens' else 'planning_or_compilation'
        elif not result['expected_tool_coverage']:result['failure_stage']='route_differs_from_fixture_expectation'
        elif not result['expected_source_visible']:result['failure_stage']='expected_source_not_visible'
        elif not result['knowledge_support_checked']:result['failure_stage']='answer_or_publication_gate'
        else:result['failure_stage']=None
        results.append(result)
    return {'scope':'synthetic development stage signals; tool expectations and model verification are not answer accuracy',
        'capture_sha256':hashlib.sha256(raw).hexdigest(),'definitions_sha256':hashlib.sha256(definitions.read_bytes()).hexdigest(),
        'expected_cases':len(expected),'captured_cases':len(rows),'complete_capture':len(rows)==len(expected),
        'counts':{k:sum(bool(r[k]) for r in results) for k in ('expected_tool_coverage','expected_source_visible','expected_source_cited','knowledge_support_checked')},
        'api_calls':sum(r['api_calls'] for r in results),'results':results}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture',type=Path,required=True);p.add_argument('--definitions',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();value=report(args.capture,args.definitions)
    args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in value.items() if k!='results'},ensure_ascii=False))


if __name__=='__main__':main()
