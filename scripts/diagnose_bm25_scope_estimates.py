import json,subprocess
from pathlib import Path
from urllib.parse import quote
from datetime import datetime,timezone
import psycopg
from psycopg import sql
from infrastructure.knowledge_applicability import source_applicability
root=Path('artifacts/eval/bm25-planner-diagnosis-2026-09-09');m=json.loads((root/'manifest.json').read_text());cfg=json.loads(subprocess.check_output(['docker','inspect','dialogpilot-target-v1-test']))[0];env=dict(v.split('=',1) for v in cfg['Config']['Env'] if '=' in v)
u='postgresql://'+quote(env['POSTGRES_USER'],safe='')+':'+quote(env['POSTGRES_PASSWORD'],safe='')+'@127.0.0.1:55432/'+m['database']
with psycopg.connect(u,autocommit=True) as c:
 c.execute('SET default_transaction_read_only=on');c.execute("SET statement_timeout='15s'")
 tenant,scope,locale=c.execute('SELECT tenant_id,scope,locale FROM retrieval.knowledge_chunk_search WHERE generation_id=%s LIMIT 1',(m['generation_id'],)).fetchone()
 base=sql.SQL('SELECT candidate_id FROM retrieval.knowledge_chunk_search d WHERE tenant_id=%s AND generation_id=%s AND scope=%s AND locale=%s');params=[tenant,m['generation_id'],scope,locale]
 identity='EXISTS (SELECT 1 FROM retrieval.knowledge_source_revisions r WHERE r.tenant_id=d.tenant_id AND r.source_id=d.source_id AND r.revision_id=d.source_revision'
 full,vs=source_applicability('d',as_of=datetime(2026,9,10,tzinfo=timezone.utc),region='CN',channel='web')
 stages=[('collection',base,params),('source_identity_only',base+sql.SQL(' AND '+identity.replace(' AND r.revision_id=d.source_revision','')+')'),params),('revision_id_only',base+sql.SQL(' AND '+identity.replace(' AND r.source_id=d.source_id','')+')'),params),('revision_identity',base+sql.SQL(' AND '+identity+')'),params),('identity_and_facets',base+sql.SQL(" AND "+identity+" AND r.withdrawn_at IS NULL AND r.region IN ('global','CN') AND r.channel IN ('global','web'))"),params),('full_applicability',base+sql.SQL(' AND ')+full,params+vs)]
 out=[]
 for label,q,p in stages:
  plan=c.execute(sql.SQL('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ')+q,p).fetchone()[0];node=plan[0]['Plan'];out.append({'stage':label,'estimated':node['Plan Rows'],'actual':node['Actual Rows'],'ms':plan[0]['Execution Time']});(root/(label+'-scope-plan.json')).write_text(json.dumps(plan,indent=2)+'\n')
 print(json.dumps(out,indent=2));(root/'scope-estimates.json').write_text(json.dumps(out,indent=2)+'\n')
