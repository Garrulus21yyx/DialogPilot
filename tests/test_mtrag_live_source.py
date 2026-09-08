"""Read-only reference backend retains source identity at publication."""
import hashlib
import pytest
from evaluation.mtrag_live_source import MtragLiveSource

def fixture():
    source=object.__new__(MtragLiveSource);source.manifest='m';source.revision='r';source.docs={'d':'abcdef'};source.hashes={'d':hashlib.sha256(b'abcdef').hexdigest()}
    pack={'index_manifest_fingerprint':'m','items':[{'text':'bcd','source_ref':{'source_id':'d','source_revision':'r','checksum':source.hashes['d'],'scope':'public','start_char':1,'end_char':4}}]}
    return source,pack

def test_source_exact_subspan_can_be_published():
    s,p=fixture();assert s.validate_publication_evidence([{'evidence_pack':p}])

@pytest.mark.parametrize('key,value',[('source_id','other'),('source_revision','old'),('checksum','wrong'),('scope','private'),('start_char',-1),('end_char',100)])
def test_changed_source_contract_fails(key,value):
    s,p=fixture();p['items'][0]['source_ref'][key]=value;assert not s._valid(p)

def test_changed_text_or_manifest_fails():
    s,p=fixture();p['items'][0]['text']='forged';assert not s._valid(p)
    s,p=fixture();p['index_manifest_fingerprint']='stale';assert not s._valid(p)

def test_generation_supplies_complete_api_policy_identity(monkeypatch):
    from types import SimpleNamespace
    from evaluation.mtrag_live_source import MtragGeneration,MtragEmbeddingIdentity
    import api.main as api
    g=MtragGeneration('manifest','generation','local-backend','bm25',MtragEmbeddingIdentity('embed'))
    monkeypatch.setattr(api,'_knowledge_store',SimpleNamespace(active_generation=lambda:g))
    monkeypatch.setattr(api,'_knowledge_retriever',None)
    p=api._knowledge_policy({},policy_version='eval')
    assert p.backend_fingerprint==g.backend_fingerprint
    assert p.lexical_provider==g.lexical_ranker and p.embedding_version=='embed'
