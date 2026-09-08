import gzip,hashlib,json,re
from pathlib import Path

ROOT=Path('artifacts/eval/rag-g4-ecommerce-pair2-2026-09-08')


def test_real_tool_policy_source_and_historical_boundary():
    for arm,weight in [('current',.25),('candidate',.75)]:
        manifest=json.loads((ROOT/arm/'manifest.json').read_text())
        docs={(d['document_id'],hashlib.sha256(d['content'].encode()).hexdigest()):d['content'] for d in manifest['source_documents']}
        rows=[json.loads(l) for l in gzip.decompress((ROOT/arm/'full-cases.jsonl.gz').read_bytes()).splitlines()]
        assert len(rows)==2
        for row in rows:
            assert row['outcome_type']=='Completed'
            assert len(row['tools'])==1 and row['tools'][0]['name']=='knowledge_search'
            tool=row['tools'][0];pack=tool['result']['data']['evidence_pack']
            assert pack['retrieval_policy']['vector_weight']==weight
            visible=json.loads(tool['result']['output_for_model'])['evidence']
            assert len(visible)==len(pack['items'])
            for item,e in zip(pack['items'],visible):
                ref=item['source_ref'];text=docs[(ref['source_id'],ref['checksum'])]
                assert hashlib.sha256(text.encode()).hexdigest()==ref['checksum']
                assert text[ref['start_char']:ref['end_char']]==item['text']==e['text']
            answer=row['outcome']['response']['response']
            cited=set(re.findall(r'\[(E[^\]]*)\]',answer))
            assert cited and cited<={e['evidence_id'] for e in visible}
            if row['case_id']=='historical-policy':
                assert tool['params']['policy_date']=='2026-03-01'
                assert any('十二元' in e['text'] for e in visible)
                assert '十二元' in answer or '12元' in answer
