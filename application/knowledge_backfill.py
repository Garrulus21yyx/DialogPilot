"""Explicit one-time conversion from full legacy source exports to v0 contracts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from application.knowledge_source import (
    KnowledgeChunkProjection,
    KnowledgeSourceContractError,
    KnowledgeSourceManifest,
    SourceRevision,
)


@dataclass(frozen=True)
class LegacySourceRecord:
    source_id: str
    title: str
    content: str
    source_type: str


@dataclass(frozen=True)
class KnowledgeBackfillBatch:
    manifest: KnowledgeSourceManifest
    sources: tuple[SourceRevision, ...]
    chunks: tuple[KnowledgeChunkProjection, ...]


def build_v0_backfill(
    records: Sequence[LegacySourceRecord],
    *,
    tenant_id: str,
    backend_id: str,
    generation_id: str,
    scope: str,
    locale: str,
    product: str,
    effective_from: datetime,
    reviewer_manifest_ref: str,
) -> KnowledgeBackfillBatch:
    """Build a deterministic full-source generation without provenance defaults."""
    required = (
        tenant_id, backend_id, generation_id, scope, locale,
        reviewer_manifest_ref,
    )
    if any(not str(value).strip() for value in required):
        raise KnowledgeSourceContractError(
            "backfill scope/locale/provenance must be explicit"
        )
    if not records:
        raise KnowledgeSourceContractError("backfill source export is empty")
    sources = tuple(sorted((
        SourceRevision.create(
            tenant_id=tenant_id,
            source_id=record.source_id,
            title=record.title,
            source_type=record.source_type,
            content=record.content,
            effective_from=effective_from,
        )
        for record in records
    ), key=lambda item: (item.source_id, item.revision_id)))
    if len({item.source_id for item in sources}) != len(sources):
        raise KnowledgeSourceContractError("backfill source IDs must be unique")
    manifest = KnowledgeSourceManifest.build(
        tenant_id=tenant_id, backend_id=backend_id,
        generation_id=generation_id, scope=scope, locale=locale,
        product=product, sources=sources,
        reviewer_manifest_ref=reviewer_manifest_ref,
    )
    chunks = tuple(
        KnowledgeChunkProjection(
            candidate_id=f"{generation_id}:{source.source_id}:0",
            source_id=source.source_id,
            revision_id=source.revision_id,
            source_checksum=source.checksum,
            start_char=0,
            end_char=len(source.content),
            lexical_document=source.content,
            provenance_sha256=hashlib.sha256(
                f"knowledge-backfill-v0\0{tenant_id}\0{backend_id}\0"
                f"{generation_id}\0{source.source_id}\0{source.revision_id}\0"
                f"{source.checksum}\0{0}\0{len(source.content)}".encode("utf-8")
            ).hexdigest(),
        )
        for source in sources
    )
    return KnowledgeBackfillBatch(manifest, sources, chunks)
