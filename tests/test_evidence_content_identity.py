"""Observation changes preserve evidence identity; source changes remain conflicts."""
import asyncio,random
from copy import deepcopy
import pytest
from application.knowledge_tool_contract import evidence_content_identity,evidence_id
from application.response_assembly import ResponseAssembler
from tests.test_knowledge_answer_boundary import board,Verifier
from tests.test_knowledge_tool_contract import evidence_result

@pytest.mark.parametrize('reverse',[False,True])
def test_observation_metadata_is_not_content_identity(reverse):
 rng=random.Random(42);base=evidence_result()['evidence_pack']['items'][0]
 for _ in range(100):
  other={**base,'rank':rng.randint(1,100),'score':rng.random(),'source_ranks':{'dense':rng.randint(1,50)},'scope_decision':'allowed_public'}
  assert evidence_content_identity(base)==evidence_content_identity(other)
  if reverse:base,other=other,base

@pytest.mark.parametrize('field,value',[('text','相反政策'),('title','别的标题'),('source_revision','v2'),('source_id','other'),('start_char',1),('end_char',90),('checksum','c'*64),('scope','private'),('applicability',{'region':'EU'})])
def test_same_citation_rejects_changed_content_through_assembly(field,value):
 original=evidence_result();changed=deepcopy(original);item=changed['evidence_pack']['items'][0]
 if field in ('text','title'):item[field]=value
 else:item['source_ref'][field]=value
 class Author:
  async def compose(self,payload):return '仅未拆封可以退货。['+evidence_id('child-1')+']'
 response=asyncio.run(ResponseAssembler(Author(),knowledge_verifier=Verifier(True),knowledge_source_validator=lambda packs:True,knowledge_reuse_validator=lambda packs:True).assemble(board('internal'),current_message='能退吗',conversation_context={'knowledge_evidence':[{'status':'CURRENT','pack':changed,'observed_at':'2026-09-01T00:00:00+00:00'}]}))
 assert not response.verified

@pytest.mark.parametrize('rank',[1,5,20])
def test_current_and_retained_same_content_compose_successfully(rank):
 changed=evidence_result();changed['evidence_pack']['query']='另一个相关问题';changed['evidence_pack']['items'][0].update(rank=rank,score=.1,source_ranks={'dense':rank})
 class Author:
  async def compose(self,payload):return '仅未拆封可以退货。['+evidence_id('child-1')+']'
 response=asyncio.run(ResponseAssembler(Author(),knowledge_verifier=Verifier(True),knowledge_source_validator=lambda packs:True,knowledge_reuse_validator=lambda packs:True).assemble(board('internal'),current_message='能退吗',conversation_context={'knowledge_evidence':[{'status':'CURRENT','pack':changed,'observed_at':'2026-09-01T00:00:00+00:00'}]}))
 assert response.verified
