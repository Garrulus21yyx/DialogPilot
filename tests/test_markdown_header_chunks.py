"""Official header boundaries with exact source provenance and bounded children."""
import pytest
from mcp.document_chunker import DocumentChunker, ChunkStructureError
from memory.context import TokenEstimator

@pytest.mark.parametrize('newline', ['\n','\r\n'])
@pytest.mark.parametrize('padding', ['', '  ', '\t'])
def test_sections_preserve_source_and_do_not_merge_products(newline,padding):
    text=newline.join([padding+'# 商城', '', '## 耳机', '退货  条件。', '| 项目 | 规则 |', '| --- | --- |', '| 耗材 | 不退 |', '', '## 咖啡机', '清水可试机。', '', '## 耳机', '第二次出现。',''])
    chunks=DocumentChunker().split(text,max_tokens=512,overlap_tokens=64,strategy='markdown_headers')
    assert ''.join(c.content for c in chunks)==text
    assert all(c.content==text[c.start_char:c.end_char] for c in chunks)
    assert all(not ('退货  条件' in c.content and '清水可试机' in c.content) for c in chunks)
    assert [c.section_path[-1] for c in chunks]==['耳机','咖啡机','耳机']
    assert all(c.chunk_index==i for i,c in enumerate(chunks))

def test_overlong_section_respects_budget_and_code_is_not_heading():
    text='# 商城\n## 耳机\n'+('试听条件。'*100)+'\n```text\n## 不是商品\n```\n## 咖啡机\n清水可试机。\n'
    chunks=DocumentChunker().split(text,max_tokens=64,overlap_tokens=8,strategy='markdown_headers')
    covered=set()
    for c in chunks:
        assert c.content==text[c.start_char:c.end_char]
        assert TokenEstimator().estimate(c.content)<=64
        assert '不是商品' not in c.section_path
        covered.update(range(c.start_char,c.end_char))
    assert covered==set(range(len(text)))
    assert any('```text\n## 不是商品\n```' in c.content for c in chunks)
    assert not any('清水可试机' in c.content and '试听条件' in c.content for c in chunks)

def test_oversized_atomic_structure_is_typed_failure():
    with pytest.raises(ChunkStructureError):
        DocumentChunker().split('## 表\n'+'| 条件 | 例外 |\n'*100,max_tokens=40,overlap_tokens=0,strategy='markdown_headers')

@pytest.mark.parametrize('source_type',['text','json'])
def test_non_markdown_keeps_literal_semantics(source_type):
    t='## A\n正文\n## B\n正文'
    chunks=DocumentChunker().split(t,max_tokens=100,overlap_tokens=0,strategy='markdown_headers',source_type=source_type)
    assert len(chunks)==1 and chunks[0].section_path==() and chunks[0].content==t


def test_alignment_loss_fails_typed_instead_of_fabricating_offsets():
    with pytest.raises(ChunkStructureError):
        DocumentChunker().split('## A\n正文\x00控制字符',max_tokens=100,overlap_tokens=0,strategy='markdown_headers')
