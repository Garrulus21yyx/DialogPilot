"""Anthropic 兼容供应商共用的 LLM 响应归一化工具。"""
from typing import Any, Iterable, List


def extract_text_content(content: Iterable[Any]) -> str:
    """兼容对象块、字典块和纯字符串，按原顺序提取文本内容。"""
    texts: List[str] = []
    for block in content or []:
        if isinstance(block, str):
            texts.append(block)
            continue

        block_type = getattr(block, "type", None)
        text = getattr(block, "text", None)
        if isinstance(block, dict):
            block_type = block.get("type", block_type)
            text = block.get("text", text)

        if isinstance(text, str) and (block_type in (None, "text")):
            texts.append(text)

    return "\n".join(t for t in texts if t)
