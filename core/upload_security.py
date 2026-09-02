"""Fail-closed content checks for the current text-only knowledge upload surface."""
from __future__ import annotations

from dataclasses import dataclass


class UploadSecurityError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class TextUploadPolicy:
    """Reject binary/polyglot signatures before UTF-8 parsing or persistence."""

    max_bytes: int = 10 * 1024 * 1024

    _MAGIC = (
        (b"MZ", "windows_executable"),
        (b"\x7fELF", "elf_executable"),
        (b"PK\x03\x04", "zip_archive"),
        (b"%PDF-", "pdf_document"),
        (b"\x89PNG\r\n\x1a\n", "png_image"),
        (b"\xff\xd8\xff", "jpeg_image"),
        (b"GIF87a", "gif_image"),
        (b"GIF89a", "gif_image"),
    )

    def validate(self, content: bytes, *, suffix: str) -> None:
        if suffix not in {".txt", ".md", ".json"}:
            raise UploadSecurityError(
                "unsupported_source_type", "only text knowledge formats are supported",
            )
        if len(content) > self.max_bytes:
            raise UploadSecurityError("upload_too_large", "upload exceeds byte limit")
        if b"\x00" in content:
            raise UploadSecurityError(
                "binary_content_rejected", "text upload contains NUL bytes",
            )
        probe = content.lstrip()[:16]
        for signature, kind in self._MAGIC:
            if probe.startswith(signature):
                raise UploadSecurityError(
                    "polyglot_content_rejected",
                    f"text upload carries a {kind} signature",
                )
