import pytest
from mcp.document_chunker import DocumentChunker, ChunkStructureError
from mcp.source_document import SourceDocument, SourceDocumentContractError
from application.knowledge_tool_contract import knowledge_query_options


@pytest.mark.parametrize('prefix_length',[0, 23, 100, 300])
@pytest.mark.parametrize('overlap',[0, 8, 16])
def test_structured_chunks_preserve_atomic_blocks_and_exact_source_coverage(prefix_length, overlap):
    text = '前文。'*prefix_length + '\n\n| 条件 | 例外 |\n| --- | --- |\n| 未拆封 | 特殊商品除外 |\n\n- 先申请\n- 再退货\n\n' + '后文。'*90
    chunker = DocumentChunker()
    chunks = chunker.split(text,max_tokens=100,overlap_tokens=overlap)
    covered = set()
    for chunk in chunks:
        assert chunk.content == text[chunk.start_char:chunk.end_char]
        covered.update(range(chunk.start_char,chunk.end_char))
    assert covered == set(range(len(text)))
    for start,end in chunker.atomic_blocks(text):
        assert any(chunk.start_char <= start and chunk.end_char >= end for chunk in chunks)
        assert all(not(start < chunk.start_char < end or start < chunk.end_char < end) for chunk in chunks)


def test_oversized_table_has_typed_import_failure():
    with pytest.raises(ChunkStructureError):
        DocumentChunker().split('| 条件 | 内容 |\n' * 100,max_tokens=50,overlap_tokens=5)


@pytest.mark.parametrize('values', [ {'as_of':'2026-01-01'}, {'as_of':'garbage'}, {'applicable_region':[]}, {'tenant_id':'invented'}, {'applicable_channel':''} ])
def test_query_options_fail_closed_on_unsupported_or_untyped_values(values):
    with pytest.raises(ValueError):
        knowledge_query_options(values)


def test_source_import_preserves_metadata_and_rejects_naive_dates():
    value = dict(title='退款',content='原文',scope='public',region='CN',product='headphones',channel='web',effective_from='2026-01-01T00:00:00+00:00')
    source = SourceDocument.from_mapping(value)
    assert (source.region,source.product,source.channel)==('CN','headphones','web')
    with pytest.raises(SourceDocumentContractError):
        SourceDocument.from_mapping({**value,'effective_from':'2026-01-01'})


@pytest.mark.parametrize('prefix',['* * * ', '- ', '| ', '# '])
@pytest.mark.parametrize('source_type',['text','json'])
def test_plain_sources_do_not_acquire_markdown_structure(prefix,source_type):
    from memory.context import TokenEstimator
    text=prefix+'literal words '*400
    chunks=DocumentChunker().split(text,max_tokens=64,overlap_tokens=8,source_type=source_type)
    assert len(chunks)>1
    assert all(not c.section_path and TokenEstimator.estimate(c.content)<=64 for c in chunks)
    covered=set()
    for c in chunks:
        assert c.content==text[c.start_char:c.end_char]
        covered.update(range(c.start_char,c.end_char))
    assert covered==set(range(len(text)))


def test_same_bytes_preserve_explicit_markdown_atomic_failure():
    text='- '+'literal words '*400
    with pytest.raises(ChunkStructureError):
        DocumentChunker().split(text,max_tokens=64,overlap_tokens=8,source_type='markdown')
    with pytest.raises(ValueError,match='unsupported source type'):
        DocumentChunker().split(text,max_tokens=64,overlap_tokens=8,source_type='unknown')
