"""Frozen lexical normalization shared by legacy BM25 and PostgreSQL FTS."""
from __future__ import annotations

import re


TOKENIZER_VERSION = "ascii-cjk-unigram-bigram-v1"

_ASCII_TOKEN = re.compile(r"[a-zA-Z0-9_#.-]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")


def tokenize_ascii_cjk_unigram_bigram(text: str) -> tuple[str, ...]:
    """Preserve the legacy token algebra byte-for-byte as a versioned contract."""
    normalized = str(text or "").lower()
    tokens = [
        token for token in _ASCII_TOKEN.findall(normalized) if token.strip(".-")
    ]
    for run in _CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index:index + 2] for index in range(len(run) - 1))
    return tuple(tokens)


def postgres_lexical_document(text: str) -> str:
    """Return pre-tokenized lexemes consumed by ``to_tsvector('simple', ...)``."""
    return " ".join(tokenize_ascii_cjk_unigram_bigram(text))


def postgres_websearch_or_query(text: str) -> str:
    """Build OR semantics without interpolating terms into SQL text."""
    return " OR ".join(tokenize_ascii_cjk_unigram_bigram(text))
