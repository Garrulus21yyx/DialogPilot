"""Source format flows through authoritative PG ingestion and index identity."""
import pytest
from tests.test_postgres_knowledge_store import store
from mcp.source_document import SourceDocument
from application.knowledge_source import KnowledgeSourceContractError


def test_plain_ingestion_succeeds_and_markdown_failure_preserves_active_generation(store):
    knowledge,pool,provider=store
    text='- '+'literal words '*500
    result=knowledge.import_documents([SourceDocument.create(source_id='plain',title='plain',content=text,source_type='text')])
    assert result.chunk_count>1
    generation=knowledge.active_generation()
    with pytest.raises(KnowledgeSourceContractError,match='exceeds chunk token budget'):
        knowledge.import_documents([SourceDocument.create(source_id='markdown',title='markdown',content=text,source_type='markdown')])
    assert knowledge.active_generation().generation_id==generation.generation_id
    assert knowledge.chunk_schema_version=='knowledge-direct-ingest-v4-source-format'
