"""Knowledge-owned immutable SourceRevision v0 and generation manifest contracts."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


class KnowledgeSourceContractError(ValueError):
    pass


def _required(*values: str) -> None:
    if any(not str(value).strip() for value in values):
        raise KnowledgeSourceContractError("knowledge source provenance is required")


def _checksum(value: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise KnowledgeSourceContractError("knowledge checksum must be lowercase SHA-256")


def _instant(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise KnowledgeSourceContractError("source instant must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceRevision:
    tenant_id: str
    source_id: str
    revision_id: str
    checksum: str
    title: str
    source_type: str
    content: str
    effective_from: datetime
    effective_to: datetime | None = None
    owner_id: str = "knowledge-ingest-owner"
    scope: str = "public"
    locale: str = "und"
    product: str = ""
    region: str = "global"
    supersedes_revision_id: str | None = None
    operations_audit_ref: str = "m2-source-revision-v0"
    schema_version: str = "knowledge-source-v0"
    channel: str = "global"

    def __post_init__(self) -> None:
        _required(
            self.tenant_id, self.source_id, self.revision_id, self.title,
            self.source_type, self.content, self.owner_id, self.scope,
            self.locale, self.region, self.operations_audit_ref,
            self.schema_version, self.channel,
        )
        from application.sales_channels import validate_sales_channel
        try:
            validate_sales_channel(self.channel, source=True)
        except ValueError as exc:
            raise KnowledgeSourceContractError(str(exc)) from exc
        if self.schema_version not in {"knowledge-source-v0", "knowledge-source-v1", "knowledge-source-v2"}:
            raise KnowledgeSourceContractError("unsupported source schema")
        if self.schema_version != "knowledge-source-v2" and self.channel != "global":
            raise KnowledgeSourceContractError("channel requires source v2")
        if self.source_id.startswith("legacy-") or self.revision_id.startswith("legacy-"):
            raise KnowledgeSourceContractError("legacy source identities are unsupported")
        _checksum(self.checksum)
        if hashlib.sha256(self.content.encode("utf-8")).hexdigest() != self.checksum:
            raise KnowledgeSourceContractError("source content checksum mismatch")
        object.__setattr__(self, "effective_from", _instant(self.effective_from))
        if self.effective_to is not None:
            object.__setattr__(self, "effective_to", _instant(self.effective_to))
        if self.effective_from.tzinfo is None:
            raise KnowledgeSourceContractError("effective_from must be timezone-aware")
        if self.effective_to is not None and (
            self.effective_to.tzinfo is None or self.effective_to <= self.effective_from
        ):
            raise KnowledgeSourceContractError("effective_to must follow effective_from")

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        source_id: str,
        title: str,
        source_type: str,
        content: str,
        effective_from: datetime,
        effective_to: datetime | None = None,
        owner_id: str = "knowledge-ingest-owner",
        scope: str = "public",
        locale: str = "und",
        product: str = "",
        region: str = "global",
        supersedes_revision_id: str | None = None,
        operations_audit_ref: str = "m2-source-revision-v0",
        schema_version: str = "knowledge-source-v0",
        channel: str = "global",
    ) -> "SourceRevision":
        effective_from = _instant(effective_from)
        effective_to = _instant(effective_to) if effective_to is not None else None
        checksum = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
        if str(schema_version) in {"knowledge-source-v1", "knowledge-source-v2"}:
            revision_prefix = "revision-v2" if schema_version == "knowledge-source-v2" else "revision-v1"
            revision_digest = _canonical_hash({
                "checksum": checksum,
                "title": str(title),
                "source_type": str(source_type),
                "effective_from": effective_from.isoformat(),
                "effective_to": (
                    effective_to.isoformat() if effective_to else None
                ),
                "owner_id": str(owner_id),
                "scope": str(scope),
                "locale": str(locale),
                "product": str(product),
                "region": str(region),
                **({"channel": str(channel)} if schema_version == "knowledge-source-v2" else {}),
                "supersedes_revision_id": supersedes_revision_id,
                "operations_audit_ref": str(operations_audit_ref),
            })
        else:
            revision_prefix = "revision-v0"
            revision_digest = checksum
        return cls(
            tenant_id=str(tenant_id), source_id=str(source_id),
            revision_id=f"{revision_prefix}-{revision_digest[:32]}", checksum=checksum,
            title=str(title), source_type=str(source_type), content=str(content),
            effective_from=effective_from, effective_to=effective_to,
            owner_id=str(owner_id), scope=str(scope), locale=str(locale),
            product=str(product), region=str(region),
            supersedes_revision_id=(
                str(supersedes_revision_id) if supersedes_revision_id else None
            ),
            operations_audit_ref=str(operations_audit_ref),
            schema_version=str(schema_version), channel=channel,
        )

    @classmethod
    def draft(
        cls,
        *,
        tenant_id: str,
        source_id: str,
        title: str,
        source_type: str,
        content: str,
        effective_from: datetime,
        owner_id: str,
        scope: str,
        locale: str,
        product: str,
        region: str,
        operations_audit_ref: str,
        effective_to: datetime | None = None,
        supersedes_revision_id: str | None = None,
    ) -> "SourceRevision":
        """Create an M5 operational draft with explicit applicability/provenance."""
        return cls.create(
            tenant_id=tenant_id, source_id=source_id, title=title,
            source_type=source_type, content=content,
            effective_from=effective_from, effective_to=effective_to,
            owner_id=owner_id, scope=scope, locale=locale, product=product,
            region=region, supersedes_revision_id=supersedes_revision_id,
            operations_audit_ref=operations_audit_ref,
            schema_version="knowledge-source-v1",
        )

    @property
    def immutable_fingerprint(self) -> str:
        return _canonical_hash({
            **{key: value for key, value in self.__dict__.items()
               if key != "channel" or self.schema_version == "knowledge-source-v2"},
            "effective_from": self.effective_from.isoformat(),
            "effective_to": (
                self.effective_to.isoformat() if self.effective_to else None
            ),
        })


@dataclass(frozen=True)
class SourceManifestEntry:
    source_id: str
    revision_id: str
    checksum: str

    def __post_init__(self) -> None:
        _required(self.source_id, self.revision_id)
        if self.source_id.startswith("legacy-") or self.revision_id.startswith("legacy-"):
            raise KnowledgeSourceContractError("legacy manifest entries are unsupported")
        _checksum(self.checksum)


@dataclass(frozen=True)
class KnowledgeSourceManifest:
    tenant_id: str
    backend_id: str
    generation_id: str
    scope: str
    locale: str
    product: str
    entries: tuple[SourceManifestEntry, ...]
    reviewer_manifest_ref: str
    manifest_hash: str
    schema_version: str = "knowledge-source-v0"

    def __post_init__(self) -> None:
        _required(
            self.tenant_id, self.backend_id, self.generation_id, self.scope,
            self.locale, self.reviewer_manifest_ref, self.schema_version,
        )
        if self.schema_version not in {"knowledge-source-v0", "knowledge-source-temporal-v2"}:
            raise KnowledgeSourceContractError("unsupported manifest schema")
        if not self.entries:
            raise KnowledgeSourceContractError("source manifest must not be empty")
        identities = [(item.source_id, item.revision_id) for item in self.entries]
        if identities != sorted(identities) or len(set(identities)) != len(identities):
            raise KnowledgeSourceContractError(
                "manifest entries must be sorted and unique"
            )
        if self.schema_version != "knowledge-source-temporal-v2" and len({item.source_id for item in self.entries}) != len(self.entries):
            raise KnowledgeSourceContractError(
                "manifest cannot contain conflicting revisions for one source"
            )
        _checksum(self.manifest_hash)
        if self.manifest_hash != self.calculate_hash(
            tenant_id=self.tenant_id,
            backend_id=self.backend_id,
            generation_id=self.generation_id,
            scope=self.scope,
            locale=self.locale,
            product=self.product,
            entries=self.entries,
            reviewer_manifest_ref=self.reviewer_manifest_ref, schema_version=self.schema_version,
        ):
            raise KnowledgeSourceContractError("source manifest hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        backend_id: str,
        generation_id: str,
        scope: str,
        locale: str,
        product: str,
        sources: Sequence[SourceRevision],
        reviewer_manifest_ref: str,
        schema_version: str = "knowledge-source-v0",
    ) -> "KnowledgeSourceManifest":
        entries = tuple(sorted((
            SourceManifestEntry(item.source_id, item.revision_id, item.checksum)
            for item in sources
        ), key=lambda item: (item.source_id, item.revision_id)))
        values = dict(
            tenant_id=str(tenant_id), backend_id=str(backend_id),
            generation_id=str(generation_id), scope=str(scope),
            locale=str(locale), product=str(product), entries=entries,
            reviewer_manifest_ref=str(reviewer_manifest_ref),
        )
        return cls(
            **values,
            manifest_hash=cls.calculate_hash(**values, schema_version=schema_version),
            schema_version=schema_version,
        )

    @staticmethod
    def calculate_hash(**values: Any) -> str:
        return _canonical_hash({
            **values,
            "entries": [item.__dict__ for item in values["entries"]],
            "schema_version": values.get("schema_version", "knowledge-source-v0"),
        })


@dataclass(frozen=True)
class KnowledgeChunkProjection:
    candidate_id: str
    source_id: str
    revision_id: str
    source_checksum: str
    start_char: int
    end_char: int
    retrieval_text: str
    section_path: tuple[str, ...]
    source_type: str
    region: str
    lexical_document: str
    provenance_sha256: str
    embedding: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        _required(
            self.candidate_id, self.source_id, self.revision_id,
            self.retrieval_text, self.source_type, self.region,
            self.lexical_document,
        )
        if any(not str(item).strip() for item in self.section_path):
            raise KnowledgeSourceContractError("chunk section path is invalid")
        _checksum(self.source_checksum)
        _checksum(self.provenance_sha256)
        if self.start_char < 0 or self.end_char <= self.start_char:
            raise KnowledgeSourceContractError("chunk span is invalid")
        if self.embedding is not None and not self.embedding:
            raise KnowledgeSourceContractError("embedding cannot be empty")

    @property
    def immutable_fingerprint(self) -> str:
        return _canonical_hash({
            **self.__dict__,
            "embedding": list(self.embedding) if self.embedding is not None else None,
        })


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
