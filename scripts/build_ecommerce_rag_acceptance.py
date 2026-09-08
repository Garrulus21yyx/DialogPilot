"""Author-owned synthetic rules -> grouped, input/gold-separated evaluation files."""
import csv,json,hashlib
from pathlib import Path
ROOT=Path('data/eval/ecommerce-rag-v1')
DEV={'policy':5,'multiturn':4,'product':5,'mixed':3,'boundary':3}
def write(name,value):
 p=ROOT/name;p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n');return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 if (ROOT/'manifest.json').exists():raise ValueError('locked dataset exists; create a version instead')
 rules=list(csv.DictReader((ROOT/'rules.tsv').open(),delimiter='\t'));assert len(rules)==60
 counts={};corpus=[];inputs={'dev':[],'heldout':[]};gold={'dev':[],'heldout':[]};fixtures={'dev':[],'heldout':[]}
 for n,r in enumerate(rules):
  category=r['category'];counts[category]=counts.get(category,0)+1;split='dev' if counts[category]<=DEV[category] else 'heldout';family=f'ec-{n+1:03d}';did='eval-shop:'+family
  corpus.append({'source_id':did,'title':r['title'],'content':r['policy'],'metadata':{'synthetic':True,'source_type':'text','effective_from':'2020-01-01T00:00:00+00:00'}})
  order_id=f'DP{9500+n}';q=r['question'].replace('{order_id}',order_id)
  for variant in range(2):
   cid=f'{family}-{variant+1}';history=[] if variant==0 else [['user',q],['assistant','我会根据可查到的资料核对，暂时还没有确认结论。']]
   message=q if variant==0 else '那就查一下，回答我刚才的问题。条件和不能确定的地方也请说清楚。'
   inputs[split].append({'id':cid,'group_id':family,'category':category,'history':history,'message':message})
   gold[split].append({'id':cid,'group_id':family,'category':category,'provenance':'MODEL_AUTHORED_SYNTHETIC','review_status':'AUTHOR_CHECKED_REQUIRES_INDEPENDENT_REVIEW','knowledge_sources':[did],'source_spans':[{'source_id':did,'start':0,'end':len(r['policy']),'quote':r['policy'],'granularity':'supporting_source_not_minimal_required_span'}],'answer_requirements':r['answer_requirements'].split('；'),'business_required':category=='mixed','forbidden_actions':['write_operation'],'hypothesis_only':family=='ec-055','metric_scope':'source_recall_and_semantic_rubric; no exact string answer grading'})
   fixtures[split].append({'id':cid,'orders':[{'order_id':order_id,'status':'SHIPPED' if n%2==0 else 'PAID','item_name':r['title'],'amount_minor':19900,'currency':'CNY'}] if category=='mixed' else []})
 hashes={'rules.tsv':hashlib.sha256((ROOT/'rules.tsv').read_bytes()).hexdigest(),'corpus.json':write('corpus.json',corpus)}
 for split in inputs:
  for suffix,v in [('inputs',inputs[split]),('gold',gold[split]),('fixtures',fixtures[split])]:hashes[split+'.'+suffix+'.json']=write(split+'.'+suffix+'.json',v)
 assert len(inputs['dev'])==40 and len(inputs['heldout'])==80
 write('manifest.json',{'version':'ecommerce-rag-v1','synthetic':True,'n':120,'independent_rule_groups':60,'splits':{'dev':{'cases':40,'groups':20},'heldout':{'cases':80,'groups':40}},'category_families':counts,'sealed_scope':'not used for tuning; author has seen authored cases; not external blind evaluation','limitations':['Two variants per rule; second turn is a contextual reference, not a separate independent scenario','Boundary cases here test textual policy ambiguity; no injected outage or actual metadata withdrawal yet','Gold spans mark supporting source, not minimal necessary evidence; complete-evidence metric requires finer annotation','Not human-approved gold; independent semantic review pending'],'sha256':hashes})
 print('Built 120 cases / 60 families, 40 dev / 80 heldout; input/gold separated')
if __name__=='__main__':main()
