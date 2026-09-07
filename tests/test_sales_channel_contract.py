"""Sales-channel authority is shared across every source/query boundary."""
import asyncio
from datetime import datetime, timezone
import random
import string

import jsonschema
import pytest

from application.sales_channels import SALES_CHANNELS
from application.knowledge_tool_contract import knowledge_query_options, knowledge_query_schema
from application.conversation_agent import planning_output_schema
from application.knowledge_source import SourceRevision
from application.hybrid_retrieval import KnowledgeSearchScope, RetrievalStatus
from application.knowledge_retriever import EvidencePackResult
from mcp.source_document import SourceDocument
from tests.test_knowledge_retriever import _request


def revision(channel):
    return SourceRevision.create(tenant_id='public', source_id='policy', title='政策',
        source_type='text', content='政策内容', effective_from=datetime(2026,1,1,tzinfo=timezone.utc),
        schema_version='knowledge-source-v2', channel=channel)


@pytest.mark.parametrize('channel', SALES_CHANNELS)
def test_catalog_roundtrip_through_all_contracts(channel):
    options = {'sales_channel': channel}
    jsonschema.validate({'query':'政策', **options}, knowledge_query_schema())
    jsonschema.validate({'status':'resolved','goals':[{'kind':'refund_policy',
        'resolved_query':'政策','knowledge_options':options}]}, planning_output_schema())
    assert knowledge_query_options(knowledge_query_options(options)) == options
    assert SourceDocument.create(source_id='policy',title='政策',content='内容',channel=channel).channel == channel
    assert revision(channel).channel == channel
    assert _request(applicable_channel=channel).applicable_channel == channel
    assert KnowledgeSearchScope(scope='public',locale='zh',applicable_channel=channel).applicable_channel == channel


def test_generated_unsupported_channels_fail_across_boundaries():
    rng=random.Random(907)
    invalid=['shipping','express','standard','chat','官网','WEB',' web ', '', None, 1, []]
    invalid += [''.join(rng.choices(string.ascii_letters,k=12)) for _ in range(50)]
    for value in invalid:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({'query':'政策','sales_channel':value},knowledge_query_schema())
        with pytest.raises(ValueError):
            knowledge_query_options({'sales_channel':value})
        # SourceDocument preserves existing surrounding-whitespace normalization.
        if value != ' web ':
            with pytest.raises(ValueError):
                SourceDocument.create(source_id='policy',title='政策',content='内容',channel=value)
        with pytest.raises(ValueError):
            revision(value)
        if value is not None:
            with pytest.raises(ValueError):
                _request(applicable_channel=value)
            with pytest.raises(ValueError):
                KnowledgeSearchScope(scope='public',locale='zh',applicable_channel=value)


def test_global_is_source_scope_and_absence_is_valid_query():
    assert revision('global').channel == 'global'
    assert knowledge_query_options({}) == {}
    for options in ({'sales_channel':'global'}, {'applicable_channel':'web'}):
        with pytest.raises(ValueError):
            knowledge_query_options(options)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({'query':'政策',**options},knowledge_query_schema())


@pytest.mark.parametrize('options,expected', [({},None),({'sales_channel':'web'},'web'),({'sales_channel':'store'},'store')])
def test_tool_maps_business_choice_and_keeps_authorization_separate(monkeypatch,options,expected):
    from api import main
    calls=[]
    async def retrieve(query, **kwargs):
        calls.append((query,kwargs))
        return EvidencePackResult(RetrievalStatus.NO_EVIDENCE,None,None,'NO_EVIDENCE')
    monkeypatch.setattr(main,'_retrieve_knowledge',retrieve)
    context={'tenant_id':'tenant','user_id':'user','authorization_fingerprint':'auth',
             'sales_channel':'web','shipping_method':'express'}
    result=asyncio.run(main._knowledge_tool_handler({'query':'如果门店购买，退货条件是什么？',**options},context))
    assert result['status']=='NO_EVIDENCE'
    assert calls[0][1]['applicable_channel']==expected
    assert calls[0][1]['tenant_id']=='tenant'
    assert calls[0][1]['authorization_fingerprint']=='auth'


@pytest.mark.parametrize('options',[{'sales_channel':'shipping'},{'applicable_channel':'web'}])
def test_invalid_filter_never_reaches_retrieval(monkeypatch,options):
    from api import main
    async def unexpected(*args,**kwargs):
        pytest.fail('invalid filter reached retrieval')
    monkeypatch.setattr(main,'_retrieve_knowledge',unexpected)
    result=asyncio.run(main._knowledge_tool_handler({'query':'加急退货运费',**options},{}))
    assert result['status']=='INVALID_CONTRACT'


def test_ingestion_api_uses_source_catalog():
    from api.main import DocInput
    assert DocInput.model_json_schema()['properties']['channel']['enum'] == [*SALES_CHANNELS,'global']
    for channel in [*SALES_CHANNELS,'global']:
        assert DocInput(title='政策',content='内容',channel=channel).channel == channel
    with pytest.raises(ValueError):
        DocInput(title='政策',content='内容',channel='shipping')
