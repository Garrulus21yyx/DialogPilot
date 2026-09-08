"""Author applicability metadata from the synthetic source declarations; never read gold."""
import argparse,hashlib,json
from pathlib import Path

CATALOG={'catalog_id':'ecommerce-complex-v2','sales_channels':{'web':'中国大陆或香港官网购买渠道','dealer':'经销商购买渠道','store':'线下特约门店购买渠道','overseas':'海外直营购买渠道'}}

def applicability(document):
    text=document['content']
    if document['metadata'].get('origin')=='WixQA':return {}
    if text.startswith('# 模拟星河商城 · 中国大陆官网 ·'):
        return {'region':'CN','channel':'web','effective_from':'2026-06-01T00:00:00+08:00'}
    if text.startswith('# 模拟星河商城 · 香港 · 官网 ·'):
        return {'region':'HK','channel':'web'}
    if text.startswith('# 模拟星河商城 · 中国大陆 · 经销商 ·'):
        return {'region':'CN','channel':'dealer'}
    if text.startswith('# 模拟星河商城 · 中国大陆 · 官网 · 2025-01旧版'):
        return {'region':'CN','channel':'web','effective_from':'2025-01-01T00:00:00+08:00','effective_to':'2026-06-01T00:00:00+08:00'}
    if document['metadata'].get('corpus_role')=='near_scope_negative':
        if '线下特约门店' in document['title']:return {'channel':'store'}
        if '海外直营' in document['title']:return {'channel':'overseas'}
    raise ValueError('unreviewed synthetic source applicability: '+document['source_id'])

def main():
    p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(exist_ok=False,parents=True)
    docs=json.loads(a.corpus.read_text())
    sidecar={d['source_id']:applicability(d) for d in docs}
    for d in docs:d['metadata'].update(sidecar[d['source_id']])
    for name,value in [('corpus.json',docs),('applicability.json',sidecar),('catalog.json',CATALOG)]:
        (a.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    (a.output/'manifest.json').write_text(json.dumps({'parent_sha256':hashlib.sha256(a.corpus.read_bytes()).hexdigest(),'corpus_sha256':hashlib.sha256((a.output/'corpus.json').read_bytes()).hexdigest(),'documents':len(docs),'scoped_documents':sum(bool(v) for v in sidecar.values()),'authority':'Synthetic author source declarations; external sources retain universal applicability; no gold read; text unchanged','time_convention':'Synthetic policy business days use Asia/Shanghai UTC+08:00'},indent=2)+'\n')
if __name__=='__main__':main()
