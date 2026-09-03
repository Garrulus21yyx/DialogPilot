"""Contextual child retrieval text remains separate from source evidence."""
from application.knowledge_retrieval_text import build_child_retrieval_text
from mcp.document_chunker import ChunkStrategy, DocumentChunker


def test_retrieval_text_adds_title_path_and_metadata_without_changing_content():
    content = "款项会按原支付渠道退回。"

    result = build_child_retrieval_text(
        title="退款政策",
        section_path=("退款政策", "到账时间"),
        content=content,
        product="钱包",
        region="cn",
    )

    assert result == (
        "[TITLE] 退款政策\n"
        "[SECTION] 到账时间\n"
        "[METADATA] product=钱包 region=cn\n"
        "[CONTENT] 款项会按原支付渠道退回。"
    )
    assert content == "款项会按原支付渠道退回。"


def test_markdown_section_path_is_owned_by_heading_structure_not_overlap():
    text = "# 退款政策\n概览。\n## 到账时间\n三个工作日。\n"
    chunker = DocumentChunker()

    root = chunker.section_path_at(
        text, 0, strategy=ChunkStrategy.FIXED_TOKENS,
    )
    child = chunker.section_path_at(
        text, text.index("三个"), strategy=ChunkStrategy.FIXED_TOKENS,
    )

    assert root == ("退款政策",)
    assert child == ("退款政策", "到账时间")
