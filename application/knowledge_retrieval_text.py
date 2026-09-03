"""Deterministic, non-authoritative text used to retrieve Knowledge children.

Source content and source-coordinate spans remain the evidence authority.  This
module only builds a derived search representation whose version is bound into
new retrieval generations.
"""
from __future__ import annotations

from collections.abc import Sequence


RETRIEVAL_TEXT_VERSION = "knowledge-child-retrieval-text-v1"


def build_child_retrieval_text(
    *,
    title: str,
    section_path: Sequence[str],
    content: str,
    product: str = "",
    region: str = "global",
) -> str:
    """Return bounded contextual text for dense and lexical child retrieval."""
    clean_title = _line(title)
    clean_path = tuple(
        value for value in (_line(item) for item in section_path)
        if value and value != clean_title
    )
    clean_content = str(content)
    if not clean_title or not clean_content.strip():
        raise ValueError("retrieval text requires title and child content")
    parts = [f"[TITLE] {clean_title}"]
    if clean_path:
        parts.append("[SECTION] " + " > ".join(clean_path))
    metadata = []
    if _line(product):
        metadata.append(f"product={_line(product)}")
    clean_region = _line(region)
    if clean_region and clean_region != "global":
        metadata.append(f"region={clean_region}")
    if metadata:
        parts.append("[METADATA] " + " ".join(metadata))
    parts.append("[CONTENT] " + clean_content)
    return "\n".join(parts)


def _line(value: str) -> str:
    return " ".join(str(value or "").split())
