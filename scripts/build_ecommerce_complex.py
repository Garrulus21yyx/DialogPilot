"""Build immutable complex synthetic RAG inputs and explicit evidence units."""
import hashlib,json
from pathlib import Path
ROOT=Path('data/eval/ecommerce-complex-v2')

def build():
    if (ROOT/'manifest.json').exists():raise ValueError('dataset already locked')
    rules=json.loads((ROOT/'rules.json').read_text());assert len(rules)==30
    docs=[]
    for part,title in enumerate(['适用条件手册','例外与兼容限制表','办理与操作指引']):
        text=f'# 模拟星河商城 · 中国大陆官网 · {title}\n\n本版适用于2026年6月1日起大陆官网购买的商品。版本和渠道不同不可混用。\n'
        # Rotate ordering to prevent aligned chunk positions across the manuals.
        for i in sorted(range(30),key=lambda i:(i+part*11)%30):
            topic,q,*clauses=rules[i]
            text+=f'\n## {topic}\n\n'
            if part==1:text+=f'| 商品 | 限制及例外 |\n| --- | --- |\n| {topic} | {clauses[part]} |\n'
            else:text+=clauses[part]+'\n'
        docs.append({'source_id':f'complex:current:{part}','title':f'大陆官网现行{title}','content':text,'metadata':{'source_type':'markdown','synthetic':True}})
    for region,channel,version in [('香港','官网','2026-06'),('中国大陆','经销商','2026-06'),('中国大陆','官网','2025-01旧版')]:
        text=f'# 模拟星河商城 · {region} · {channel} · {version}\n\n本页仅用于所标渠道与版本，不代表其他渠道规则。\n'
        for topic,q,*clauses in rules:
            text+=f'\n## {topic}\n{topic}本渠道须向原销售方提交申请，不能直接使用大陆官网受理入口。{topic}的适配配置及服务范围以本渠道单独签署的清单为准。\n'
        docs.append({'source_id':f'complex:distractor:{len(docs)}','title':f'{region}{channel}{version}资料','content':text,'metadata':{'source_type':'markdown','synthetic':True}})
    inputs={'dev':[],'heldout':[]};labels={'dev':[],'heldout':[]}
    for i,(topic,q,*clauses) in enumerate(rules):
        split='dev' if i%3==0 else 'heldout';family=f'cx-{i+1:02}'
        scope='我只问2026年6月1日起中国大陆官网现行规则，不问经销商或香港渠道，也不查询具体订单。'
        questions=[(scope+q,[]),(f'不是，我问的是{topic}，按刚才提到的现行渠道，把条件、例外和处理步骤一起说清楚。',[['user',scope+q],['assistant','需要确认，您仍在问刚才那类商品吗？']]),('以上只是我的假设，并不表示实际发生。请按这些假设说明适用条件、例外及处理办法。',[['user',scope+q]]),('如果我还不能确认是否满足这些前提，请列出需要核实的条件、相关例外和后续处理步骤，不要直接认定我符合条件。',[['user',scope+q]])]
        evidence=[]
        for part,quote in enumerate(clauses):
            body=docs[part]['content'];assert body.count(quote)==1;start=body.index(quote)
            evidence.append({'unit_id':f'{family}:u{part}','source_id':docs[part]['source_id'],'start_char':start,'end_char':start+len(quote),'quote':quote})
        for variant,(message,history) in enumerate(questions,1):
            cid=f'{family}-{variant}';inputs[split].append({'id':cid,'message':message,'history':history})
            labels[split].append({'id':cid,'group_id':family,'topic':topic,'category':['direct','correction','hypothetical','missing_conditions'][variant-1],'evidence_units':evidence,'answer_requirements':clauses,'forbidden_sources':[d['source_id'] for d in docs[3:]],'scope':'2026-06-01 onward mainland official channel','synthetic':True})
    for name,value in [('corpus.json',docs),*[(f'{s}.inputs.json',inputs[s]) for s in inputs],*[(f'{s}.gold.json',labels[s]) for s in labels]]:
        (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    manifest={'scope':'synthetic cross-document evidence retrieval; source themes partly reused, new composition; no business execution','dev':40,'heldout':80,'groups':30,'units_per_case':3,'human_review':'pending','heldout_status':'locked_not_run','document_chars':[len(d['content']) for d in docs],'sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in ROOT.glob('*.json')}}
    (ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))
if __name__=='__main__':build()
