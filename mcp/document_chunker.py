"""Authoritative document chunking primitives shared by ingestion and evaluation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from memory.context import TokenEstimator


class ChunkStructureError(ValueError):
    """A supported atomic structure cannot fit the configured chunk budget."""


class ChunkStrategy(str, Enum):
    """Bounded chunk strategies supported by the production knowledge owner."""

    FIXED_TOKENS = "fixed_tokens"
    STRUCTURE_AWARE = "structure_aware"


@dataclass(frozen=True)
class DocumentChunk:
    """A chunk projection whose offsets remain anchored in the source document."""

    content: str
    start_char: int
    end_char: int
    chunk_index: int
    section_path: tuple[str, ...] = ()


class DocumentChunker:
    """Split source documents while preserving exact source-character provenance."""

    def __init__(self, token_estimator: Optional[TokenEstimator] = None):
        self._token_estimator = token_estimator or TokenEstimator()

    def split(
        self,
        text: str,
        *,
        max_tokens: int,
        overlap_tokens: int,
        strategy: ChunkStrategy | str = ChunkStrategy.STRUCTURE_AWARE,
        source_type: str = "markdown",
    ) -> List[DocumentChunk]:
        """Return bounded chunks with exact half-open source offsets.

        Ingestion must pass its authoritative source_type. The low-level default
        retains Markdown helper compatibility; literal text/decoded JSON content
        uses paragraph boundaries without acquiring Markdown atomic structures.
        """
        # Preserve the exact source string: evidence spans and audit provenance are
        # expressed in original-document coordinates. Trimming here would shift
        # every later offset for documents with leading whitespace or markup.
        text = str(text or "")
        strategy = ChunkStrategy(strategy)
        if source_type not in {"text", "markdown", "json"}:
            raise ValueError("unsupported source type for chunking")
        max_tokens = int(max_tokens)
        overlap_tokens = int(overlap_tokens)
        if not text.strip():
            return []
        if max_tokens < 1 or overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("chunk token budgets require 0 <= overlap_tokens < max_tokens")
        if self._token_estimator.estimate(text) <= max_tokens:
            return [DocumentChunk(
                text, 0, len(text), 0,
                self.section_path_at(text, 0, strategy=strategy, source_type=source_type),
            )]

        protected = self.atomic_blocks(text) if strategy is ChunkStrategy.STRUCTURE_AWARE and source_type == "markdown" else ()
        if any(self._token_estimator.estimate(text[a:b]) > max_tokens for a, b in protected):
            raise ChunkStructureError("table, list block or fenced code exceeds chunk token budget")
        chunks: List[DocumentChunk] = []
        start = 0
        while start < len(text):
            max_end = self.max_fitting_end(text, start, max_tokens)
            if max_end <= start:
                raise RuntimeError("chunker could not make progress")
            end = (
                self.preferred_break(text, start, max_end)
                if strategy is ChunkStrategy.STRUCTURE_AWARE
                else max_end
            )
            for a, b in protected:
                if a < end < b:
                    end = a if a > start else b
                    break
            chunks.append(DocumentChunk(
                text[start:end], start, end, len(chunks),
                self.section_path_at(text, start, strategy=strategy, source_type=source_type),
            ))
            if end >= len(text):
                break
            next_start = self.overlap_start(text, start, end, overlap_tokens)
            for a, b in protected:
                if a < next_start < b:
                    next_start = a if self._token_estimator.estimate(text[a:end]) <= overlap_tokens else b
                    break
            if next_start <= start:
                next_start = end
            start = next_start

        if any(self._token_estimator.estimate(chunk.content) > max_tokens for chunk in chunks):
            raise RuntimeError("chunker produced an over-budget chunk")
        return chunks

    @staticmethod
    def atomic_blocks(text: str) -> tuple[tuple[int, int], ...]:
        """Recognize bounded Markdown tables, contiguous lists and fenced code.

        Text remains untouched. Oversized structures require explicit source
        preparation or a larger import budget, not a broken header/condition pair.
        """
        blocks = []
        lines = text.splitlines(keepends=True)
        offset = 0
        begin = None
        kind = None
        fence = None
        for line in lines:
            stripped = line.lstrip()
            marker = re.match(r"(`{3,}|~{3,})", stripped)
            if kind == "fence":
                offset += len(line)
                if marker and marker.group(0)[0] == fence[0] and len(marker.group(0)) >= len(fence):
                    blocks.append((begin, offset)); begin = kind = fence = None
                continue
            current = ("fence" if marker else "table" if "|" in line and line.strip() else
                       "list" if re.match(r"(?:[-+*]|\d+[.)])\s+", stripped) else None)
            if kind == "list" and line.strip() and line[:1].isspace():
                current = "list"
            if kind and current != kind:
                blocks.append((begin, offset)); begin = kind = None
            if current and begin is None:
                begin, kind = offset, current
                fence = marker.group(0) if marker else None
            offset += len(line)
        if begin is not None:
            blocks.append((begin, offset))
        return tuple(blocks)

    def max_fitting_end(self, text: str, start: int, max_tokens: int) -> int:
        """Find the longest suffix end whose estimated token count stays bounded."""
        low, high = start + 1, len(text)
        best = start
        while low <= high:
            middle = (low + high) // 2
            if self._token_estimator.estimate(text[start:middle]) <= max_tokens:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        return best

    @staticmethod
    def preferred_break(text: str, start: int, max_end: int) -> int:
        """Prefer paragraph, sentence, or whitespace boundaries in the last 40%."""
        minimum = start + max(1, int((max_end - start) * 0.60))
        window = text[minimum:max_end]
        matches = list(re.finditer(r"\n+|[。！？.!?]+|\s+", window))
        return minimum + matches[-1].end() if matches else max_end

    def overlap_start(self, text: str, start: int, end: int, overlap_tokens: int) -> int:
        """Find the longest suffix within the configured overlap token budget."""
        if overlap_tokens <= 0:
            return end
        low, high = start, end
        best = end
        while low <= high:
            middle = (low + high) // 2
            if self._token_estimator.estimate(text[middle:end]) <= overlap_tokens:
                best = middle
                high = middle - 1
            else:
                low = middle + 1
        return best

    @staticmethod
    def section_path_at(
        text: str,
        start: int,
        *,
        strategy: ChunkStrategy | str,
        source_type: str = "markdown",
    ) -> tuple[str, ...]:
        """Return the Markdown heading path governing ``start``.

        Plain text has no authoritative heading syntax, so it deliberately gets
        no invented path.  ATX headings are deterministic and remain useful even
        when the chunk strategy itself is fixed-token.
        """
        if source_type not in {"text", "markdown", "json"}:
            raise ValueError("unsupported source type for heading extraction")
        if source_type != "markdown":
            return ()
        del strategy  # Heading ownership is independent from boundary strategy.
        levels: dict[int, str] = {}
        cursor = 0
        for line in str(text).splitlines(keepends=True):
            if cursor > max(0, int(start)):
                break
            match = re.match(r"^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", line.rstrip("\r\n"))
            if match:
                level = len(match.group(1))
                heading = " ".join(match.group(2).split())
                levels = {key: value for key, value in levels.items() if key < level}
                if heading:
                    levels[level] = heading
            cursor += len(line)
        return tuple(levels[level] for level in sorted(levels))
